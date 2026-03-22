from __future__ import annotations

import ast
import copy
import hashlib
import inspect
import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass
from html import escape
from pathlib import Path
from types import CodeType
from typing import Callable, ClassVar, Mapping, Union

from djule.compiler.render_plan import (
    AttrExprPart,
    ComponentPlan,
    ExprPart,
    NodePart,
    PlanPart,
    StaticPart,
)
from djule.parser.ast_nodes import (
    AssignStmt,
    AttributeNode,
    BlockItem,
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
    MarkupNode,
    Module,
    PythonExpr,
    ReturnStmt,
    TextNode,
)
from djule.parser.parser import DjuleParser


class SafeHtml(str):
    """A rendered HTML fragment that should not be escaped again."""


ExternalComponent = Union[Callable[..., object], ComponentDef]


@dataclass(frozen=True)
class ImportedComponentRef:
    renderer: "DjuleRenderer"
    component_name: str


@dataclass
class RendererError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


class DjuleRenderer:
    """Render the current Djule AST subset to HTML."""

    CACHE_VERSION: ClassVar[int] = 4
    _parsed_module_cache: ClassVar[dict[Path, tuple[int, int, Module]]] = {}
    _compiled_expr_cache: ClassVar[dict[str, CodeType]] = {}
    _entry_plan_cache: ClassVar[
        dict[tuple[Path, str], tuple[int, int, ComponentPlan, tuple[tuple[str, int, int], ...]]]
    ] = {}

    DEFAULT_BUILTINS: Mapping[str, object] = {
        "bool": bool,
        "dict": dict,
        "enumerate": enumerate,
        "int": int,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "range": range,
        "set": set,
        "str": str,
        "sum": sum,
        "tuple": tuple,
    }

    def __init__(
        self,
        module: Module,
        component_registry: Mapping[str, ExternalComponent] | None = None,
        builtins: Mapping[str, object] | None = None,
        *,
        module_path: Path | None = None,
        search_paths: list[Path] | None = None,
        renderer_cache: dict[Path, "DjuleRenderer"] | None = None,
    ) -> None:
        self.module = module
        self.module_path = module_path.resolve() if module_path else None
        self.internal_components = {component.name: component for component in module.components}
        self.component_registry = dict(component_registry or {})
        self.builtins = dict(self.DEFAULT_BUILTINS)
        if builtins:
            self.builtins.update(builtins)
        self.search_paths = [path.resolve() for path in (search_paths or [])]
        self.renderer_cache = renderer_cache if renderer_cache is not None else {}
        if self.module_path is not None:
            self.renderer_cache[self.module_path] = self
        self.auto_component_registry: dict[str, ImportedComponentRef] = {}
        self.auto_module_registry: dict[str, "DjuleRenderer"] = {}
        self.imports_loaded = False
        self._instance_component_plans: dict[str, ComponentPlan] = {}
        self._plan_dependency_paths: set[Path] | None = None

    @classmethod
    def from_source(
        cls,
        source: str,
        component_registry: Mapping[str, ExternalComponent] | None = None,
        builtins: Mapping[str, object] | None = None,
        *,
        search_paths: list[Path] | None = None,
    ) -> "DjuleRenderer":
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
        component_registry: Mapping[str, ExternalComponent] | None = None,
        builtins: Mapping[str, object] | None = None,
        *,
        search_paths: list[Path] | None = None,
        renderer_cache: dict[Path, "DjuleRenderer"] | None = None,
    ) -> "DjuleRenderer":
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
            version_path.write_text(json.dumps(expected, separators=(",", ":"), sort_keys=True))
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
            cache_path.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True))
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
        """Mirror Python's import-root model as closely as practical.

        Djule absolute imports resolve from a global list of roots, similar to
        Python's sys.path. The current working directory is represented in
        sys.path as an empty string, so we normalize that to Path.cwd().
        """
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

    def render(
        self,
        component_name: str | None = None,
        props: Mapping[str, object] | None = None,
    ) -> str:
        target_name = component_name or self._default_component_name()
        return str(self._render_component_by_name(target_name, dict(props or {}), persist_plan=True))

    def _get_component_plan(self, component_name: str, *, persist: bool) -> ComponentPlan | None:
        if component_name in self._instance_component_plans:
            return self._instance_component_plans[component_name]

        component = self.internal_components.get(component_name)
        if component is None:
            return None

        if not persist or self.module_path is None:
            plan = self._compile_component_plan(component_name)
            self._instance_component_plans[component_name] = plan
            return plan

        plan = self._load_cached_entry_plan(self.module_path, component_name)
        self._instance_component_plans[component_name] = plan
        return plan

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
            cache_path.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True))
        except OSError:
            return

    def _default_component_name(self) -> str:
        if "Page" in self.internal_components:
            return "Page"
        if not self.module.components:
            raise RendererError("Module has no components to render")
        return self.module.components[0].name

    def _render_component_by_name(
        self,
        component_name: str,
        props: dict[str, object],
        *,
        persist_plan: bool = False,
    ) -> SafeHtml:
        component = self._resolve_component(component_name)
        if component is None:
            raise RendererError(f"Unknown component '{component_name}'")

        self._validate_component_props(component_name, component, props)

        if isinstance(component, ComponentDef):
            return self._render_component_def(component, props, persist_plan=persist_plan)

        if isinstance(component, ImportedComponentRef):
            return component.renderer._render_component_by_name(
                component.component_name,
                props,
                persist_plan=persist_plan,
            )

        result = component(**props)
        if isinstance(result, SafeHtml):
            return result
        return SafeHtml(str(result))

    def _render_component_def(
        self,
        component: ComponentDef,
        props: dict[str, object],
        *,
        persist_plan: bool = False,
    ) -> SafeHtml:
        env = dict(props)

        if "children" in env and "children" not in component.params:
            raise RendererError(
                f"Component '{component.name}' received nested content, but it does not declare a 'children' prop"
            )

        if "children" in component.params and "children" not in env:
            env["children"] = SafeHtml("")

        missing = [name for name in component.params if name not in env]
        if missing:
            missing_args = ", ".join(missing)
            raise RendererError(f"Missing prop(s) for component '{component.name}': {missing_args}")

        component_plan = self._get_component_plan(component.name, persist=persist_plan)
        if component_plan is None:
            self._execute_statements(component.body, env)
            return self._render_return(component.return_stmt, env)
        if component_plan.requires_runtime_body:
            self._execute_statements(component.body, env)
        return self._render_component_plan(component_plan, env)

    def _execute_statements(self, statements: list[object], env: dict[str, object]) -> None:
        for statement in statements:
            self._execute_statement(statement, env)

    def _execute_statement(self, statement: object, env: dict[str, object]) -> None:
        if isinstance(statement, AssignStmt):
            if isinstance(statement.value, PythonExpr):
                env[statement.target] = self._eval_python_expr(statement.value.source, env)
            else:
                env[statement.target] = self._render_markup_node(statement.value, env)
            return

        if isinstance(statement, ExprStmt):
            self._eval_python_expr(statement.value.source, env)
            return

        if isinstance(statement, IfStmt):
            branch = statement.body if self._eval_python_expr(statement.test.source, env) else statement.orelse
            self._execute_statements(branch, env)
            return

        if isinstance(statement, ForStmt):
            iterable = self._eval_python_expr(statement.iter.source, env)
            for item in iterable:
                env[statement.target] = item
                self._execute_statements(statement.body, env)
            return

        raise RendererError(f"Unsupported statement type: {type(statement)!r}")

    def _render_return(self, statement: ReturnStmt, env: dict[str, object]) -> SafeHtml:
        return self._render_markup_node(statement.value, env)

    def _render_component_plan(self, component_plan: ComponentPlan, env: dict[str, object]) -> SafeHtml:
        fragments: list[str] = []
        for part in component_plan.parts:
            fragments.append(self._render_plan_part(part, env))
        return SafeHtml("".join(fragments))

    def _render_plan_part(self, part: PlanPart, env: dict[str, object]) -> str:
        if isinstance(part, StaticPart):
            return part.value

        if isinstance(part, ExprPart):
            return str(self._render_expression_value(self._eval_python_expr(part.source, env)))

        if isinstance(part, AttrExprPart):
            value = self._eval_python_expr(part.source, env)
            if value is None:
                return ""
            return escape(str(value), quote=True)

        if isinstance(part, NodePart):
            return str(self._render_markup_node(part.node, env))

        raise RendererError(f"Unsupported render plan part: {type(part)!r}")

    def _render_markup_node(self, node: MarkupNode, env: dict[str, object]) -> SafeHtml:
        if isinstance(node, TextNode):
            return SafeHtml(node.value)

        if isinstance(node, ExpressionNode):
            return self._render_expression_value(self._eval_python_expr(node.source, env))

        if isinstance(node, BlockNode):
            return self._render_block_node(node, env)

        if isinstance(node, ElementNode):
            return self._render_element_node(node, env)

        if isinstance(node, ComponentNode):
            return self._render_component_node(node, env)

        raise RendererError(f"Unsupported markup node: {type(node)!r}")
    def _render_block_node(self, node: BlockNode, env: dict[str, object]) -> SafeHtml:
        fragments: list[str] = []
        self._execute_block_items(node.statements, env, fragments)
        return SafeHtml("".join(fragments))

    def _render_element_node(self, node: ElementNode, env: dict[str, object]) -> SafeHtml:
        rendered_attributes = self._render_attributes(node.attributes, env)
        rendered_children = self._render_children(node.children, env)
        return SafeHtml(f"<{node.tag}{rendered_attributes}>{rendered_children}</{node.tag}>")

    def _render_component_node(self, node: ComponentNode, env: dict[str, object]) -> SafeHtml:
        props = self._resolve_props(node.attributes, env)
        component = self._resolve_component(node.name)
        if component is None:
            raise RendererError(f"Unknown component '{node.name}'")

        if node.children:
            if not self._component_accepts_children(component):
                raise RendererError(
                    f"Component '{node.name}' was used with nested content, but it does not declare a 'children' prop"
                )
            props["children"] = self._render_children(node.children, env)
        return self._render_resolved_component(node.name, component, props)

    def _render_children(self, children: list[MarkupNode], env: dict[str, object]) -> SafeHtml:
        if not children:
            return SafeHtml("")

        return SafeHtml("".join(str(self._render_markup_node(child, env)) for child in children))

    def _render_attributes(self, attributes: list[AttributeNode], env: dict[str, object]) -> str:
        parts = []
        for attribute in attributes:
            value = self._resolve_attribute_value(attribute, env)
            parts.append(f' {attribute.name}="{escape(value, quote=True)}"')
        return "".join(parts)

    def _resolve_props(self, attributes: list[AttributeNode], env: dict[str, object]) -> dict[str, object]:
        props: dict[str, object] = {}
        for attribute in attributes:
            if isinstance(attribute.value, PythonExpr):
                props[attribute.name] = self._eval_python_expr(attribute.value.source, env)
            else:
                props[attribute.name] = ast.literal_eval(attribute.value)
        return props

    def _resolve_attribute_value(self, attribute: AttributeNode, env: dict[str, object]) -> str:
        if isinstance(attribute.value, PythonExpr):
            value = self._eval_python_expr(attribute.value.source, env)
        else:
            value = ast.literal_eval(attribute.value)

        if value is None:
            return ""
        return str(value)

    def _render_expression_value(self, value: object) -> SafeHtml:
        if value is None:
            return SafeHtml("")

        if isinstance(value, SafeHtml):
            return value

        if isinstance(value, (list, tuple)):
            rendered_items = [self._render_expression_value(item) for item in value]
            return SafeHtml("".join(rendered_items))

        return SafeHtml(escape(str(value)))

    def _compile_entry_plan(
        self,
        component_name: str,
    ) -> tuple[ComponentPlan, tuple[tuple[str, int, int], ...]]:
        previous_dependencies = self._plan_dependency_paths
        self._plan_dependency_paths = set()
        if self.module_path is not None:
            self._plan_dependency_paths.add(self.module_path)

        try:
            component = self.internal_components.get(component_name)
            if component is None:
                raise RendererError(f"Unknown component '{component_name}'")
            parts, requires_runtime_body = self._compile_component_with_bindings(component, {})
            plan = ComponentPlan(
                name=component.name,
                parts=self._merge_static_parts(parts),
                requires_runtime_body=requires_runtime_body,
            )
            dependencies = self._dependency_snapshot(self._plan_dependency_paths)
        finally:
            self._plan_dependency_paths = previous_dependencies

        return plan, dependencies

    def _compile_component_plan(self, component_name: str) -> ComponentPlan:
        component = self.internal_components.get(component_name)
        if component is None:
            raise RendererError(f"Unknown component '{component_name}'")

        parts, requires_runtime_body = self._compile_component_with_bindings(component, {})
        return ComponentPlan(
            name=component.name,
            parts=self._merge_static_parts(parts),
            requires_runtime_body=requires_runtime_body,
        )

    def _compile_component_with_bindings(
        self,
        component: ComponentDef,
        bindings: dict[str, tuple[str, object]],
    ) -> tuple[list[PlanPart], bool]:
        body_bindings, fully_flattened = self._compile_component_body_bindings(component.body, bindings)
        active_bindings = body_bindings if fully_flattened else bindings
        return self._compile_markup_plan(component.return_stmt.value, active_bindings), not fully_flattened

    def _compile_component_body_bindings(
        self,
        statements: list[object],
        bindings: dict[str, tuple[str, object]],
    ) -> tuple[dict[str, tuple[str, object]], bool]:
        compiled = dict(bindings)

        for statement in statements:
            if not isinstance(statement, AssignStmt):
                return dict(bindings), False

            if isinstance(statement.value, PythonExpr):
                compiled[statement.target] = ("expr", self._rewrite_python_expr(statement.value.source, compiled))
                continue

            if isinstance(statement.value, (TextNode, ExpressionNode, ElementNode, ComponentNode, BlockNode)):
                compiled[statement.target] = ("plan", self._compile_markup_plan(statement.value, compiled))
                continue

            return dict(bindings), False

        return compiled, True

    def _compile_markup_plan(
        self,
        node: MarkupNode,
        bindings: dict[str, tuple[str, object]],
    ) -> list[PlanPart]:
        if isinstance(node, TextNode):
            return [StaticPart(node.value)]

        if isinstance(node, ExpressionNode):
            return self._compile_expression_parts(node.source, bindings)

        if isinstance(node, BlockNode):
            return [NodePart(node)]

        if isinstance(node, ElementNode):
            parts: list[PlanPart] = [StaticPart(f"<{node.tag}")]
            for attribute in node.attributes:
                parts.extend(self._compile_attribute_parts(attribute, bindings))
            parts.append(StaticPart(">"))
            parts.extend(self._compile_children_plan(node.children, bindings))
            parts.append(StaticPart(f"</{node.tag}>"))
            return self._merge_static_parts(parts)

        if isinstance(node, ComponentNode):
            return self._compile_component_node_parts(node, bindings)

        raise RendererError(f"Unsupported markup node for plan compilation: {type(node)!r}")

    def _compile_children_plan(
        self,
        children: list[MarkupNode],
        bindings: dict[str, tuple[str, object]],
    ) -> list[PlanPart]:
        parts: list[PlanPart] = []
        for child in children:
            parts.extend(self._compile_markup_plan(child, bindings))
        return self._merge_static_parts(parts)

    def _compile_expression_parts(
        self,
        source: str,
        bindings: dict[str, tuple[str, object]],
    ) -> list[PlanPart]:
        binding = self._binding_for_expression(source, bindings)
        if binding is None:
            return [ExprPart(source)]

        binding_type, value = binding
        if binding_type == "literal":
            return [StaticPart(str(self._render_expression_value(value)))]
        if binding_type == "expr":
            return [ExprPart(str(value))]
        if binding_type in {"children", "plan"}:
            return list(value)
        return [ExprPart(source)]

    def _compile_attribute_parts(
        self,
        attribute: AttributeNode,
        bindings: dict[str, tuple[str, object]],
    ) -> list[PlanPart]:
        if not isinstance(attribute.value, PythonExpr):
            literal_value = ast.literal_eval(attribute.value)
            return [StaticPart(f' {attribute.name}="{escape("" if literal_value is None else str(literal_value), quote=True)}"')]

        binding = self._binding_for_expression(attribute.value.source, bindings)
        if binding is not None:
            binding_type, value = binding
            if binding_type == "literal":
                literal_value = "" if value is None else str(value)
                return [StaticPart(f' {attribute.name}="{escape(literal_value, quote=True)}"')]
            if binding_type == "expr":
                return [StaticPart(f' {attribute.name}="'), AttrExprPart(str(value)), StaticPart('"')]

        return [StaticPart(f' {attribute.name}="'), AttrExprPart(attribute.value.source), StaticPart('"')]

    def _compile_component_node_parts(
        self,
        node: ComponentNode,
        bindings: dict[str, tuple[str, object]],
    ) -> list[PlanPart]:
        component = self._resolve_component(node.name)
        if component is None:
            return [NodePart(node)]

        if node.children and not self._component_accepts_children(component):
            raise RendererError(
                f"Component '{node.name}' was used with nested content, but it does not declare a 'children' prop"
            )

        if isinstance(component, ImportedComponentRef) and component.renderer.module_path is not None:
            self._track_plan_dependency(component.renderer.module_path)

        prop_bindings = self._build_component_bindings(node, bindings)

        static_props = self._static_props_from_bindings(prop_bindings)
        if static_props is not None and (
            isinstance(component, ImportedComponentRef)
            or not isinstance(component, ComponentDef)
            or bool(component.body)
        ):
            rendered = self._render_resolved_component(node.name, component, static_props)
            return [StaticPart(str(rendered))]

        inlined = self._try_inline_component_plan(component, prop_bindings)
        if inlined is not None:
            return self._merge_static_parts(inlined)

        return [NodePart(node)]

    def _build_component_bindings(
        self,
        node: ComponentNode,
        bindings: dict[str, tuple[str, object]],
    ) -> dict[str, tuple[str, object]]:
        resolved: dict[str, tuple[str, object]] = {}

        for attribute in node.attributes:
            if isinstance(attribute.value, PythonExpr):
                binding = self._binding_for_expression(attribute.value.source, bindings)
                if binding is not None:
                    resolved[attribute.name] = binding
                else:
                    resolved[attribute.name] = ("expr", attribute.value.source)
            else:
                resolved[attribute.name] = ("literal", ast.literal_eval(attribute.value))

        if node.children:
            resolved["children"] = ("children", self._compile_children_plan(node.children, bindings))

        return resolved

    def _static_props_from_bindings(
        self,
        bindings: dict[str, tuple[str, object]],
    ) -> dict[str, object] | None:
        props: dict[str, object] = {}
        for name, (binding_type, value) in bindings.items():
            if binding_type == "literal":
                props[name] = value
                continue
            if binding_type == "children":
                child_parts = list(value)
                if any(not isinstance(part, StaticPart) for part in child_parts):
                    return None
                props[name] = SafeHtml("".join(part.value for part in child_parts))
                continue
            if binding_type == "plan":
                plan_parts = list(value)
                if any(not isinstance(part, StaticPart) for part in plan_parts):
                    return None
                props[name] = SafeHtml("".join(part.value for part in plan_parts))
                continue
            return None
        return props

    def _try_inline_component_plan(
        self,
        component: ExternalComponent | ImportedComponentRef,
        bindings: dict[str, tuple[str, object]],
    ) -> list[PlanPart] | None:
        if isinstance(component, ImportedComponentRef):
            resolved = component.renderer._resolve_component(component.component_name)
            if isinstance(resolved, ComponentDef):
                parts, requires_runtime_body = component.renderer._compile_component_with_bindings(resolved, bindings)
                if not requires_runtime_body:
                    return parts
            return None

        if isinstance(component, ComponentDef):
            parts, requires_runtime_body = self._compile_component_with_bindings(component, bindings)
            if not requires_runtime_body:
                return parts

        return None

    @staticmethod
    def _binding_for_expression(
        source: str,
        bindings: dict[str, tuple[str, object]],
    ) -> tuple[str, object] | None:
        if source.isidentifier():
            return bindings.get(source)
        return None

    @classmethod
    def _rewrite_python_expr(
        cls,
        source: str,
        bindings: dict[str, tuple[str, object]],
    ) -> str:
        try:
            tree = ast.parse(source, mode="eval")
        except SyntaxError:
            return source

        class BindingRewriter(ast.NodeTransformer):
            def visit_Name(self, node: ast.Name) -> ast.AST:
                if not isinstance(node.ctx, ast.Load):
                    return node

                binding = bindings.get(node.id)
                if binding is None:
                    return node

                binding_type, value = binding
                if binding_type == "expr":
                    replacement = ast.parse(str(value), mode="eval").body
                    return ast.copy_location(copy.deepcopy(replacement), node)
                if binding_type == "literal":
                    replacement = ast.parse(repr(value), mode="eval").body
                    return ast.copy_location(replacement, node)
                return node

        rewritten = BindingRewriter().visit(tree)
        ast.fix_missing_locations(rewritten)
        try:
            return ast.unparse(rewritten)
        except Exception:
            return source

    def _track_plan_dependency(self, path: Path) -> None:
        if self._plan_dependency_paths is not None:
            self._plan_dependency_paths.add(path.resolve())

    @staticmethod
    def _merge_static_parts(parts: list[PlanPart]) -> list[PlanPart]:
        merged: list[PlanPart] = []
        for part in parts:
            if (
                merged
                and isinstance(merged[-1], StaticPart)
                and isinstance(part, StaticPart)
            ):
                merged[-1] = StaticPart(merged[-1].value + part.value)
            else:
                merged.append(part)
        return merged

    def _execute_block_items(
        self,
        items: list[BlockItem],
        env: dict[str, object],
        fragments: list[str],
    ) -> None:
        for item in items:
            self._execute_block_item(item, env, fragments)

    def _execute_block_item(
        self,
        item: BlockItem,
        env: dict[str, object],
        fragments: list[str],
    ) -> None:
        if isinstance(item, (TextNode, ExpressionNode, ElementNode, ComponentNode, BlockNode)):
            fragments.append(str(self._render_markup_node(item, env)))
            return

        if isinstance(item, EmbeddedExprNode):
            fragments.append(str(self._render_expression_value(self._eval_python_expr(item.source, env))))
            return

        if isinstance(item, EmbeddedAssignNode):
            if isinstance(item.value, PythonExpr):
                env[item.target] = self._eval_python_expr(item.value.source, env)
            else:
                env[item.target] = self._render_markup_node(item.value, env)
            return

        if isinstance(item, EmbeddedIfNode):
            branch = item.body if self._eval_python_expr(item.test.source, env) else item.orelse
            self._execute_block_items(branch, env, fragments)
            return

        if isinstance(item, EmbeddedForNode):
            iterable = self._eval_python_expr(item.iter.source, env)
            for value in iterable:
                env[item.target] = value
                self._execute_block_items(item.body, env, fragments)
            return

        raise RendererError(f"Unsupported embedded block item: {type(item)!r}")

    def _eval_python_expr(self, source: str, env: dict[str, object]) -> object:
        scope = {"__builtins__": self.builtins, **env}
        try:
            code = self._compiled_expr_cache.get(source)
            if code is None:
                filename = str(self.module_path) if self.module_path is not None else "<djule>"
                code = compile(source, filename, "eval")
                self._compiled_expr_cache[source] = code
            return eval(code, scope, scope)
        except Exception as exc:  # pragma: no cover - error path exercised by users, not fixtures
            raise RendererError(f"Failed to evaluate expression '{source}': {exc}") from exc

    def _resolve_component(self, name: str) -> ExternalComponent | ImportedComponentRef | None:
        if name in self.internal_components:
            return self.internal_components[name]

        if name in self.component_registry:
            return self.component_registry[name]

        self._load_auto_imports()
        if name in self.auto_component_registry:
            return self.auto_component_registry[name]

        if "." in name:
            namespace, component_name = name.rsplit(".", 1)
            module_renderer = self.auto_module_registry.get(namespace)
            if module_renderer is not None:
                return ImportedComponentRef(renderer=module_renderer, component_name=component_name)

        return None

    def _load_auto_imports(self) -> None:
        if self.imports_loaded:
            return

        for import_node in self.module.imports:
            module_renderer = self._load_imported_module(import_node.module)
            if isinstance(import_node, ImportFrom):
                for name in import_node.names:
                    if name not in module_renderer.internal_components:
                        raise RendererError(
                            f"Imported component '{name}' was not found in module '{import_node.module}'"
                        )
                    self.auto_component_registry[name] = ImportedComponentRef(
                        renderer=module_renderer,
                        component_name=name,
                    )
                continue

            namespace = self._module_import_namespace(import_node)
            self.auto_module_registry[namespace] = module_renderer

        self.imports_loaded = True

    @staticmethod
    def _module_import_namespace(import_node: ImportModule) -> str:
        if import_node.alias:
            return import_node.alias
        if import_node.module.startswith("."):
            raise RendererError(
                f"Relative module import '{import_node.module}' must use 'as <alias>' to create a usable component namespace"
            )
        return import_node.module

    def _load_imported_module(self, module_name: str) -> "DjuleRenderer":
        module_path = self._resolve_module_path(module_name)
        cached_renderer = self.renderer_cache.get(module_path)
        if cached_renderer is not None:
            return cached_renderer

        return self.from_file(
            module_path,
            component_registry=self.component_registry,
            builtins=self.builtins,
            search_paths=self.search_paths,
            renderer_cache=self.renderer_cache,
        )

    def _resolve_module_path(self, module_name: str) -> Path:
        if module_name.startswith("."):
            return self._resolve_relative_module_path(module_name)

        module_parts = module_name.split(".")
        candidates: list[Path] = []

        for base_path in self.search_paths:
            candidates.append(base_path.joinpath(*module_parts).with_suffix(".djule"))
            candidates.append(base_path.joinpath(*module_parts, "__init__.djule"))

        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()

        searched_paths = ", ".join(str(path) for path in candidates) or "<no search paths configured>"
        raise RendererError(f"Could not resolve imported module '{module_name}'. Searched: {searched_paths}")

    def _resolve_relative_module_path(self, module_name: str) -> Path:
        if self.module_path is None:
            raise RendererError(
                f"Could not resolve relative import '{module_name}' without a source file path"
            )

        leading_dots = len(module_name) - len(module_name.lstrip("."))
        remainder = module_name[leading_dots:]
        module_parts = remainder.split(".") if remainder else []

        base_path = self.module_path.parent
        for _ in range(max(leading_dots - 1, 0)):
            base_path = base_path.parent

        candidates = []
        if module_parts:
            candidates.append(base_path.joinpath(*module_parts).with_suffix(".djule"))
            candidates.append(base_path.joinpath(*module_parts, "__init__.djule"))
        else:
            candidates.append(base_path / "__init__.djule")

        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()

        searched_paths = ", ".join(str(path) for path in candidates)
        raise RendererError(f"Could not resolve imported module '{module_name}'. Searched: {searched_paths}")

    def _render_resolved_component(
        self,
        component_name: str,
        component: ExternalComponent | ImportedComponentRef,
        props: dict[str, object],
    ) -> SafeHtml:
        self._validate_component_props(component_name, component, props)

        if isinstance(component, ComponentDef):
            return self._render_component_def(component, props)

        if isinstance(component, ImportedComponentRef):
            return component.renderer._render_component_by_name(component.component_name, props)

        result = component(**props)
        if isinstance(result, SafeHtml):
            return result
        return SafeHtml(str(result))

    def _validate_component_props(
        self,
        component_name: str,
        component: ExternalComponent | ImportedComponentRef,
        props: dict[str, object],
    ) -> None:
        if "children" in props and not self._component_accepts_children(component):
            raise RendererError(
                f"Component '{component_name}' received nested content, but it does not declare a 'children' prop"
            )

    @staticmethod
    def _component_accepts_children(component: ExternalComponent | ImportedComponentRef) -> bool:
        if isinstance(component, ImportedComponentRef):
            resolved = component.renderer._resolve_component(component.component_name)
            if resolved is None:
                return False
            return component.renderer._component_accepts_children(resolved)

        if isinstance(component, ComponentDef):
            return "children" in component.params

        try:
            signature = inspect.signature(component)
        except (TypeError, ValueError):
            return False

        for parameter in signature.parameters.values():
            if parameter.kind == inspect.Parameter.VAR_KEYWORD:
                return True

        return "children" in signature.parameters


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
