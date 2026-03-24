from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

from djule.compiler.render_plan import AttrExprPart, ComponentPlan, ExprPart, NodePart, StaticPart
from djule.parser.ast_nodes import (
    AssignStmt,
    AttributeNode,
    BlockNode,
    ComponentDef,
    ComponentNode,
    ElementNode,
    EmbeddedAssignNode,
    EmbeddedExprNode,
    EmbeddedForNode,
    EmbeddedIfNode,
    ExprStmt,
    ExpressionNode,
    ForStmt,
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
    @classmethod
    def from_source(
        cls,
        source: str,
        component_registry=None,
        builtins=None,
        *,
        search_paths: list[Path] | None = None,
    ):
        module = DjuleParser.from_source(source).parse()
        resolved_search_paths = [path.resolve() for path in (search_paths or cls._default_search_paths())]
        return cls(
            module,
            component_registry=component_registry,
            builtins=builtins,
            search_paths=resolved_search_paths,
        )

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        component_registry=None,
        builtins=None,
        *,
        search_paths: list[Path] | None = None,
        renderer_cache: dict[Path, "DjuleRenderer"] | None = None,
    ):
        resolved_path = Path(path).resolve()
        module = cls._load_cached_module(resolved_path)
        resolved_search_paths = [base.resolve() for base in (search_paths or cls._default_search_paths())]
        return cls(
            module,
            component_registry=component_registry,
            builtins=builtins,
            module_path=resolved_path,
            search_paths=resolved_search_paths,
            renderer_cache=renderer_cache,
        )

    @classmethod
    def clear_caches(cls) -> None:
        cls._parsed_module_cache.clear()
        cls._compiled_expr_cache.clear()
        cls._entry_plan_cache.clear()

    @classmethod
    def cache_stats(cls) -> dict[str, int]:
        return {
            "parsed_modules": len(cls._parsed_module_cache),
            "compiled_expressions": len(cls._compiled_expr_cache),
            "render_plans": len(cls._entry_plan_cache),
        }

    @classmethod
    def _load_cached_module(cls, path: Path) -> Module:
        resolved_path = path.resolve()
        stat = resolved_path.stat()
        cache_entry = cls._parsed_module_cache.get(resolved_path)
        if cache_entry is not None:
            cached_mtime_ns, cached_size, cached_module = cache_entry
            if cached_mtime_ns == stat.st_mtime_ns and cached_size == stat.st_size:
                return cached_module

        disk_cached_module = cls._load_disk_cached_module(resolved_path, stat)
        if disk_cached_module is not None:
            cls._parsed_module_cache[resolved_path] = (stat.st_mtime_ns, stat.st_size, disk_cached_module)
            return disk_cached_module

        module = DjuleParser.from_file(resolved_path).parse()
        cls._parsed_module_cache[resolved_path] = (stat.st_mtime_ns, stat.st_size, module)
        cls._write_disk_cached_module(resolved_path, stat, module)
        return module

    @classmethod
    def _cache_root(cls) -> Path:
        cache_root = Path(os.environ.get("DJULE_CACHE_DIR", ".djule-cache")).resolve()
        cls._ensure_cache_layout(cache_root)
        (cache_root / "modules").mkdir(parents=True, exist_ok=True)
        (cache_root / "plans").mkdir(parents=True, exist_ok=True)
        return cache_root

    @classmethod
    def _ensure_cache_layout(cls, cache_root: Path) -> None:
        version_path = cache_root / "version.json"
        expected = {"version": cls.CACHE_VERSION}

        if version_path.exists():
            try:
                if json.loads(version_path.read_text()) == expected:
                    return
            except (OSError, json.JSONDecodeError):
                pass

        for subdir in ("static", "manifests", "plans"):
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
        digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()
        return cls._cache_root() / "modules" / f"{digest}.json"

    @classmethod
    def _plan_cache_path(cls, path: Path, component_name: str) -> Path:
        digest = hashlib.sha256(f"{path}::{component_name}".encode("utf-8")).hexdigest()
        return cls._cache_root() / "plans" / f"{digest}.json"

    @classmethod
    def _load_disk_cached_module(cls, path: Path, stat_result: os.stat_result) -> Module | None:
        cache_path = cls._module_cache_path(path)
        if not cache_path.exists():
            return None

        try:
            payload = json.loads(cache_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

        if payload.get("source_path") != str(path):
            return None
        if payload.get("mtime_ns") != stat_result.st_mtime_ns or payload.get("size") != stat_result.st_size:
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
        for path_str, mtime_ns, size in dependencies:
            path = Path(path_str)
            try:
                stat = path.stat()
            except OSError:
                return False
            if stat.st_mtime_ns != mtime_ns or stat.st_size != size:
                return False
        return True

    def _load_cached_entry_plan(self, path: Path, component_name: str) -> ComponentPlan:
        resolved_path = path.resolve()
        stat = resolved_path.stat()

        cache_key = (resolved_path, component_name)
        cache_entry = self._entry_plan_cache.get(cache_key)
        if cache_entry is not None:
            cached_mtime_ns, cached_size, cached_plan, cached_deps = cache_entry
            if (
                cached_mtime_ns == stat.st_mtime_ns
                and cached_size == stat.st_size
                and self._dependencies_are_current(cached_deps)
            ):
                return cached_plan

        disk_cached = self._load_disk_cached_entry_plan(resolved_path, component_name, stat)
        if disk_cached is not None:
            plan, dependencies = disk_cached
            self._entry_plan_cache[cache_key] = (stat.st_mtime_ns, stat.st_size, plan, dependencies)
            return plan

        plan, dependencies = self._compile_entry_plan(component_name)
        self._entry_plan_cache[cache_key] = (stat.st_mtime_ns, stat.st_size, plan, dependencies)
        self._write_disk_cached_entry_plan(resolved_path, component_name, stat, plan, dependencies)
        return plan

    @classmethod
    def _load_disk_cached_entry_plan(
        cls,
        path: Path,
        component_name: str,
        stat_result: os.stat_result,
    ) -> tuple[ComponentPlan, tuple[tuple[str, int, int], ...]] | None:
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
        if payload.get("mtime_ns") != stat_result.st_mtime_ns or payload.get("size") != stat_result.st_size:
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
        if not cls._dependencies_are_current(dependency_tuple):
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
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            temp_path.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True))
            temp_path.replace(path)
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)


_CACHED_NODE_TYPES = {
    "PythonExpr": PythonExpr,
    "ImportFrom": ImportFrom,
    "ImportModule": ImportModule,
    "AttributeNode": AttributeNode,
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
