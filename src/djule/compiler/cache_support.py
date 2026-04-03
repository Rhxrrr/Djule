from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import asdict
from pathlib import Path

from djule.compiler.render_plan import AttrExprPart, ComponentPlan, ExprPart, NodePart, StaticPart
from djule.parser.ast_nodes import (
    AssignStmt,
    AttributeNode,
    BlockNode,
    ComponentDef,
    ComponentNode,
    DeclarationNode,
    ElementNode,
    EmbeddedAssignNode,
    EmbeddedExprNode,
    EmbeddedForNode,
    EmbeddedIfNode,
    ExprStmt,
    ExpressionNode,
    ForStmt,
    FragmentNode,
    ImportFrom,
    ImportModule,
    IfStmt,
    Module,
    PythonExpr,
    ReturnStmt,
    TextNode,
)
from djule.parser.parser import DjuleParser


class DjuleCacheMixin:
    """Shared parsed-module and render-plan cache helpers for `DjuleRenderer`."""
    @classmethod
    def from_source(
        cls,
        source: str,
        component_registry=None,
        builtins=None,
        importables=None,
        *,
        search_paths: list[Path] | None = None,
        cache_validate: bool = True,
    ):
        """Construct a renderer directly from raw Djule source text."""
        module = DjuleParser.from_source(source).parse()
        resolved_search_paths = [path.resolve() for path in (search_paths or cls._default_search_paths())]
        return cls(
            module,
            component_registry=component_registry,
            builtins=builtins,
            importables=importables,
            search_paths=resolved_search_paths,
            cache_validate=cache_validate,
        )

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        component_registry=None,
        builtins=None,
        importables=None,
        *,
        search_paths: list[Path] | None = None,
        renderer_cache: dict[Path, "DjuleRenderer"] | None = None,
        cache_validate: bool = True,
    ):
        """Construct a renderer from a file, reusing parsed-module caches when possible."""
        resolved_path = Path(path).resolve()
        module = cls._load_cached_module(resolved_path, cache_validate=cache_validate)
        resolved_search_paths = [base.resolve() for base in (search_paths or cls._default_search_paths())]
        return cls(
            module,
            component_registry=component_registry,
            builtins=builtins,
            importables=importables,
            module_path=resolved_path,
            search_paths=resolved_search_paths,
            renderer_cache=renderer_cache,
            cache_validate=cache_validate,
        )

    @classmethod
    def clear_caches(cls) -> None:
        """Clear all in-memory caches and the on-disk Djule cache tree."""
        cls._parsed_module_cache.clear()
        cls._compiled_expr_cache.clear()
        cls._entry_plan_cache.clear()
        cls._trusted_module_cache_paths.clear()
        cls._trusted_entry_plan_cache_keys.clear()
        cls._observed_invalidation_token = None
        cache_root = Path(os.environ.get("DJULE_CACHE_DIR", ".djule-cache")).resolve()
        if cache_root.exists():
            shutil.rmtree(cache_root, ignore_errors=True)

    @classmethod
    def cache_stats(cls) -> dict[str, int]:
        """Return quick counts for the main in-memory renderer caches."""
        return {
            "parsed_modules": len(cls._parsed_module_cache),
            "compiled_expressions": len(cls._compiled_expr_cache),
            "render_plans": len(cls._entry_plan_cache),
        }

    @classmethod
    def _load_cached_module(cls, path: Path, *, cache_validate: bool = True) -> Module:
        """Load a parsed module, validating it against disk when requested."""
        cls._sync_external_invalidations()
        resolved_path = path.resolve()
        cache_entry = cls._parsed_module_cache.get(resolved_path)
        if cache_entry is not None and (not cache_validate) and resolved_path in cls._trusted_module_cache_paths:
            return cache_entry[2]

        stat = resolved_path.stat()
        if cache_entry is not None:
            cached_mtime_ns, cached_size, cached_module = cache_entry
            if cached_mtime_ns == stat.st_mtime_ns and cached_size == stat.st_size:
                if not cache_validate:
                    cls._trusted_module_cache_paths.add(resolved_path)
                return cached_module
            cls._parsed_module_cache.pop(resolved_path, None)
            cls._trusted_module_cache_paths.discard(resolved_path)

        if (not cache_validate) and resolved_path in cls._trusted_module_cache_paths:
            disk_cached_module = cls._load_disk_cached_module(resolved_path, stat_result=None)
            if disk_cached_module is not None:
                cls._parsed_module_cache[resolved_path] = (stat.st_mtime_ns, stat.st_size, disk_cached_module)
                return disk_cached_module
            cls._trusted_module_cache_paths.discard(resolved_path)

        disk_cached_module = cls._load_disk_cached_module(
            resolved_path,
            stat_result=None if not cache_validate else stat,
        )
        if disk_cached_module is not None:
            cls._parsed_module_cache[resolved_path] = (stat.st_mtime_ns, stat.st_size, disk_cached_module)
            if not cache_validate:
                cls._trusted_module_cache_paths.add(resolved_path)
            return disk_cached_module

        module = DjuleParser.from_file(resolved_path).parse()
        cls._parsed_module_cache[resolved_path] = (stat.st_mtime_ns, stat.st_size, module)
        if not cache_validate:
            cls._trusted_module_cache_paths.add(resolved_path)
        cls._write_disk_cached_module(resolved_path, stat, module)
        return module

    @classmethod
    def _cache_root(cls) -> Path:
        """Return the Djule cache root directory, creating the expected layout if needed."""
        cache_root = Path(os.environ.get("DJULE_CACHE_DIR", ".djule-cache")).resolve()
        cls._ensure_cache_layout(cache_root)
        (cache_root / "modules").mkdir(parents=True, exist_ok=True)
        (cache_root / "plans").mkdir(parents=True, exist_ok=True)
        return cache_root

    @classmethod
    def _ensure_cache_layout(cls, cache_root: Path) -> None:
        """Create or migrate the cache directory layout for the current cache version.

        Old incompatible cache directories are removed when the on-disk version
        marker does not match the renderer's current cache schema version.
        """
        version_path = cache_root / "version.json"
        expected = {"version": cls.CACHE_VERSION}

        if version_path.exists():
            try:
                if json.loads(version_path.read_text()) == expected:
                    return
            except (OSError, json.JSONDecodeError):
                pass

        for subdir in ("modules", "plans", "static", "manifests"):
            target = cache_root / subdir
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)

        cache_root.mkdir(parents=True, exist_ok=True)
        try:
            cls._write_json_file(version_path, expected)
        except OSError:
            return

    @classmethod
    def _module_cache_path(cls, path: Path) -> Path:
        """Return the disk cache file path for one parsed source module."""
        digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()
        return cls._cache_root() / "modules" / f"{digest}.json"

    @classmethod
    def _plan_cache_path(cls, path: Path, component_name: str) -> Path:
        """Return the disk cache file path for one compiled entry component plan."""
        digest = hashlib.sha256(f"{path}::{component_name}".encode("utf-8")).hexdigest()
        return cls._cache_root() / "plans" / f"{digest}.json"

    @classmethod
    def _invalidation_state_path(cls) -> Path:
        """Return the shared invalidation token file used across Djule processes."""
        return cls._cache_root() / "invalidation.json"

    @classmethod
    def _load_invalidation_token(cls) -> int | None:
        """Read the latest shared invalidation token from disk, if one exists."""
        path = cls._invalidation_state_path()
        if not path.exists():
            return None

        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

        token = payload.get("token")
        return token if isinstance(token, int) else None

    @classmethod
    def _record_invalidation_token(cls, changed_path: Path | None = None) -> None:
        """Publish a shared invalidation token so other processes can drop trust."""
        token = time.time_ns()
        payload: dict[str, object] = {"token": token}
        if changed_path is not None:
            payload["changed_path"] = str(changed_path.resolve())

        try:
            cls._write_json_file(cls._invalidation_state_path(), payload)
        except OSError:
            return

        cls._observed_invalidation_token = token

    @classmethod
    def _sync_external_invalidations(cls) -> None:
        """Drop local trusted cache markers when another process invalidated Djule cache."""
        token = cls._load_invalidation_token()
        if token == cls._observed_invalidation_token:
            return

        cls._parsed_module_cache.clear()
        cls._entry_plan_cache.clear()
        cls._trusted_module_cache_paths.clear()
        cls._trusted_entry_plan_cache_keys.clear()
        cls._observed_invalidation_token = token

    @classmethod
    def _load_disk_cached_module(cls, path: Path, *, stat_result: os.stat_result | None) -> Module | None:
        """Load a parsed module from disk cache if one exists and is still current."""
        cache_path = cls._module_cache_path(path)
        if not cache_path.exists():
            return None

        try:
            payload = json.loads(cache_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

        if payload.get("source_path") != str(path):
            return None
        if stat_result is not None and (
            payload.get("mtime_ns") != stat_result.st_mtime_ns or payload.get("size") != stat_result.st_size
        ):
            return None

        module_data = payload.get("module")
        if not isinstance(module_data, dict):
            return None

        try:
            module = cls._deserialize_cached_node(module_data)
        except (KeyError, TypeError, ValueError):
            return None

        if not isinstance(module, Module):
            return None
        return module

    @classmethod
    def _write_disk_cached_module(cls, path: Path, stat_result: os.stat_result, module: Module) -> None:
        """Persist a parsed module to disk cache using the source file metadata as a key."""
        payload = {
            "source_path": str(path),
            "mtime_ns": stat_result.st_mtime_ns,
            "size": stat_result.st_size,
            "module": asdict(module),
        }
        cache_path = cls._module_cache_path(path)
        try:
            cls._write_json_file(cache_path, payload)
        except OSError:
            return

    @classmethod
    def _deserialize_cached_node(cls, value: object) -> object:
        """Rebuild cached AST node dataclasses from JSON-friendly payloads."""
        if isinstance(value, list):
            return [cls._deserialize_cached_node(item) for item in value]

        if isinstance(value, dict):
            node_type = value.get("type")
            if isinstance(node_type, str) and node_type in _CACHED_NODE_TYPES:
                node_cls = _CACHED_NODE_TYPES[node_type]
                kwargs = {
                    key: cls._deserialize_cached_node(inner)
                    for key, inner in value.items()
                    if key != "type"
                }
                return node_cls(**kwargs)
            return {key: cls._deserialize_cached_node(inner) for key, inner in value.items()}

        return value

    @classmethod
    def _deserialize_cached_plan(cls, value: object) -> object:
        """Rebuild cached render-plan dataclasses and nested AST nodes from disk data."""
        if isinstance(value, list):
            return [cls._deserialize_cached_plan(item) for item in value]

        if isinstance(value, dict):
            node_type = value.get("type")
            if isinstance(node_type, str) and node_type in _CACHED_PLAN_TYPES:
                node_cls = _CACHED_PLAN_TYPES[node_type]
                kwargs = {
                    key: cls._deserialize_cached_plan(inner)
                    for key, inner in value.items()
                    if key != "type"
                }
                return node_cls(**kwargs)
            if isinstance(node_type, str) and node_type in _CACHED_NODE_TYPES:
                return cls._deserialize_cached_node(value)
            return {key: cls._deserialize_cached_plan(inner) for key, inner in value.items()}

        return value

    @classmethod
    def _default_search_paths(cls) -> list[Path]:
        """Mirror Python's import-root model as closely as practical."""
        env_paths = os.environ.get("DJULE_PATH")
        if env_paths:
            return [Path(entry).resolve() for entry in env_paths.split(os.pathsep) if entry]

        search_paths: list[Path] = []
        seen: set[Path] = set()

        for entry in sys.path:
            candidate = Path.cwd() if entry == "" else Path(entry)
            resolved = candidate.resolve()
            if resolved in seen or not resolved.exists() or not resolved.is_dir():
                continue
            search_paths.append(resolved)
            seen.add(resolved)

        return search_paths or [Path.cwd().resolve()]

    @classmethod
    def _dependency_snapshot(cls, paths: set[Path]) -> tuple[tuple[str, int, int], ...]:
        """Capture stable file metadata for all plan dependency paths."""
        snapshot: list[tuple[str, int, int]] = []
        for path in sorted(path.resolve() for path in paths):
            try:
                stat = path.stat()
            except OSError:
                continue
            snapshot.append((str(path), stat.st_mtime_ns, stat.st_size))
        return tuple(snapshot)

    @staticmethod
    def _dependencies_are_current(dependencies: tuple[tuple[str, int, int], ...]) -> bool:
        """Return whether every cached dependency still matches its saved metadata."""
        for path_str, mtime_ns, size in dependencies:
            path = Path(path_str)
            try:
                stat = path.stat()
            except OSError:
                return False
            if stat.st_mtime_ns != mtime_ns or stat.st_size != size:
                return False
        return True

    def _load_cached_entry_plan(
        self,
        path: Path,
        component_name: str,
    ) -> ComponentPlan:
        """Load a compiled entry plan, validating it per request unless disabled."""
        self._sync_external_invalidations()
        resolved_path = path.resolve()
        cache_validation_enabled = self.cache_validate

        cache_key = (resolved_path, component_name)
        cache_entry = self._entry_plan_cache.get(cache_key)
        if cache_entry is not None and (cache_validation_enabled is False) and cache_key in self._trusted_entry_plan_cache_keys:
            return cache_entry[2]

        stat = resolved_path.stat()
        if cache_entry is not None:
            cached_mtime_ns, cached_size, cached_plan, cached_deps = cache_entry
            if (
                cached_mtime_ns == stat.st_mtime_ns
                and cached_size == stat.st_size
                and (
                    not cache_validation_enabled
                    or self._dependencies_are_current(cached_deps)
                )
            ):
                if not cache_validation_enabled:
                    self._trusted_entry_plan_cache_keys.add(cache_key)
                return cached_plan
            self._entry_plan_cache.pop(cache_key, None)
            self._trusted_entry_plan_cache_keys.discard(cache_key)

        disk_cached = self._load_disk_cached_entry_plan(
            resolved_path,
            component_name,
            stat_result=None if not cache_validation_enabled else stat,
        )
        if disk_cached is not None:
            plan, dependencies = disk_cached
            self._entry_plan_cache[cache_key] = (stat.st_mtime_ns, stat.st_size, plan, dependencies)
            if not cache_validation_enabled:
                self._trusted_entry_plan_cache_keys.add(cache_key)
            return plan

        plan, dependencies = self._compile_entry_plan(component_name)
        self._entry_plan_cache[cache_key] = (stat.st_mtime_ns, stat.st_size, plan, dependencies)
        if not cache_validation_enabled:
            self._trusted_entry_plan_cache_keys.add(cache_key)
        self._write_disk_cached_entry_plan(resolved_path, component_name, stat, plan, dependencies)
        return plan

    @classmethod
    def _load_disk_cached_entry_plan(
        cls,
        path: Path,
        component_name: str,
        *,
        stat_result: os.stat_result | None,
    ) -> tuple[ComponentPlan, tuple[tuple[str, int, int], ...]] | None:
        """Load an entry render plan from disk when one exists for this source path and component."""
        cache_path = cls._plan_cache_path(path, component_name)
        if not cache_path.exists():
            return None

        try:
            payload = json.loads(cache_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

        if payload.get("source_path") != str(path):
            return None
        if payload.get("component_name") != component_name:
            return None
        if stat_result is not None and (
            payload.get("mtime_ns") != stat_result.st_mtime_ns or payload.get("size") != stat_result.st_size
        ):
            return None

        deps_raw = payload.get("dependencies")
        if not isinstance(deps_raw, list):
            return None

        dependencies: list[tuple[str, int, int]] = []
        for item in deps_raw:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("path"), str)
                or not isinstance(item.get("mtime_ns"), int)
                or not isinstance(item.get("size"), int)
            ):
                return None
            dependencies.append((item["path"], item["mtime_ns"], item["size"]))

        dependency_tuple = tuple(dependencies)
        if stat_result is not None and not cls._dependencies_are_current(dependency_tuple):
            return None
        plan_raw = payload.get("plan")
        if not isinstance(plan_raw, dict):
            return None

        try:
            plan = cls._deserialize_cached_plan(plan_raw)
        except (KeyError, TypeError, ValueError):
            return None

        if not isinstance(plan, ComponentPlan):
            return None
        return plan, dependency_tuple

    @classmethod
    def _write_disk_cached_entry_plan(
        cls,
        path: Path,
        component_name: str,
        stat_result: os.stat_result,
        plan: ComponentPlan,
        dependencies: tuple[tuple[str, int, int], ...],
    ) -> None:
        """Persist a compiled entry render plan and its dependency snapshot to disk."""
        payload = {
            "source_path": str(path),
            "component_name": component_name,
            "mtime_ns": stat_result.st_mtime_ns,
            "size": stat_result.st_size,
            "dependencies": [
                {"path": dep_path, "mtime_ns": dep_mtime_ns, "size": dep_size}
                for dep_path, dep_mtime_ns, dep_size in dependencies
            ],
            "plan": asdict(plan),
        }
        cache_path = cls._plan_cache_path(path, component_name)
        try:
            cls._write_json_file(cache_path, payload)
        except OSError:
            return

    @staticmethod
    def _write_json_file(path: Path, payload: dict[str, object]) -> None:
        """Write a cache payload atomically by replacing the target via a temp file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            temp_path.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True))
            temp_path.replace(path)
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)

    @classmethod
    def invalidate_path_caches(cls, path: Path) -> set[tuple[Path, str]]:
        """Invalidate one changed source path and any cached page plans that depend on it."""
        resolved_path = path.resolve()
        invalidated_entry_keys: set[tuple[Path, str]] = set()

        cls._parsed_module_cache.pop(resolved_path, None)
        cls._trusted_module_cache_paths.discard(resolved_path)
        module_cache_path = cls._module_cache_path(resolved_path)
        if module_cache_path.exists():
            module_cache_path.unlink(missing_ok=True)

        for cache_key, (_mtime_ns, _size, _plan, dependencies) in list(cls._entry_plan_cache.items()):
            source_path, _component_name = cache_key
            dependency_paths = {Path(dep_path).resolve() for dep_path, _dep_mtime, _dep_size in dependencies}
            if source_path == resolved_path or resolved_path in dependency_paths:
                invalidated_entry_keys.add(cache_key)

        plans_dir = cls._cache_root() / "plans"
        for cache_path in plans_dir.glob("*.json"):
            try:
                payload = json.loads(cache_path.read_text())
            except (OSError, json.JSONDecodeError):
                continue

            source_path_raw = payload.get("source_path")
            component_name = payload.get("component_name")
            dependencies = payload.get("dependencies")
            if not isinstance(source_path_raw, str) or not isinstance(component_name, str) or not isinstance(dependencies, list):
                continue

            dependency_paths: set[Path] = set()
            for item in dependencies:
                if isinstance(item, dict) and isinstance(item.get("path"), str):
                    dependency_paths.add(Path(item["path"]).resolve())

            source_path = Path(source_path_raw).resolve()
            if source_path == resolved_path or resolved_path in dependency_paths:
                invalidated_entry_keys.add((source_path, component_name))
                cache_path.unlink(missing_ok=True)

        for cache_key in invalidated_entry_keys:
            cls._entry_plan_cache.pop(cache_key, None)
            cls._trusted_entry_plan_cache_keys.discard(cache_key)

        cls._record_invalidation_token(resolved_path)
        return invalidated_entry_keys


_CACHED_NODE_TYPES = {
    "PythonExpr": PythonExpr,
    "ImportFrom": ImportFrom,
    "ImportModule": ImportModule,
    "AttributeNode": AttributeNode,
    "DeclarationNode": DeclarationNode,
    "FragmentNode": FragmentNode,
    "TextNode": TextNode,
    "ExpressionNode": ExpressionNode,
    "EmbeddedExprNode": EmbeddedExprNode,
    "ElementNode": ElementNode,
    "ComponentNode": ComponentNode,
    "AssignStmt": AssignStmt,
    "ExprStmt": ExprStmt,
    "IfStmt": IfStmt,
    "ForStmt": ForStmt,
    "ReturnStmt": ReturnStmt,
    "ComponentDef": ComponentDef,
    "EmbeddedAssignNode": EmbeddedAssignNode,
    "EmbeddedIfNode": EmbeddedIfNode,
    "EmbeddedForNode": EmbeddedForNode,
    "BlockNode": BlockNode,
    "Module": Module,
}

_CACHED_PLAN_TYPES = {
    "StaticPart": StaticPart,
    "ExprPart": ExprPart,
    "AttrExprPart": AttrExprPart,
    "NodePart": NodePart,
    "ComponentPlan": ComponentPlan,
}
