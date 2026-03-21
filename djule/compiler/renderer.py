from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Callable, Mapping, Union

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


@dataclass
class RendererError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


class DjuleRenderer:
    """Render the current Djule AST subset to HTML."""

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
        self.auto_component_registry: dict[str, Callable[..., SafeHtml]] = {}
        self.imports_loaded = False

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
        resolved_search_paths = [path.resolve() for path in (search_paths or [])]
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
        module = DjuleParser.from_file(resolved_path).parse()
        resolved_search_paths = [base.resolve() for base in (search_paths or [resolved_path.parent])]
        return cls(
            module,
            component_registry=component_registry,
            builtins=builtins,
            module_path=resolved_path,
            search_paths=resolved_search_paths,
            renderer_cache=renderer_cache,
        )

    def render(
        self,
        component_name: str | None = None,
        props: Mapping[str, object] | None = None,
    ) -> str:
        target_name = component_name or self._default_component_name()
        return str(self._render_component_by_name(target_name, dict(props or {})))

    def _default_component_name(self) -> str:
        if "Page" in self.internal_components:
            return "Page"
        if not self.module.components:
            raise RendererError("Module has no components to render")
        return self.module.components[0].name

    def _render_component_by_name(self, component_name: str, props: dict[str, object]) -> SafeHtml:
        component = self._resolve_component(component_name)
        if component is None:
            raise RendererError(f"Unknown component '{component_name}'")

        self._validate_component_props(component_name, component, props)

        if isinstance(component, ComponentDef):
            return self._render_component_def(component, props)

        result = component(**props)
        if isinstance(result, SafeHtml):
            return result
        return SafeHtml(str(result))

    def _render_component_def(self, component: ComponentDef, props: dict[str, object]) -> SafeHtml:
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

        self._execute_statements(component.body, env)
        return self._render_return(component.return_stmt, env)

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
        rendered_children = "".join(self._render_markup_node(child, env) for child in node.children)
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
            props["children"] = SafeHtml("".join(self._render_markup_node(child, env) for child in node.children))
        return self._render_resolved_component(node.name, component, props)

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
            return eval(source, scope, scope)
        except Exception as exc:  # pragma: no cover - error path exercised by users, not fixtures
            raise RendererError(f"Failed to evaluate expression '{source}': {exc}") from exc

    def _resolve_component(self, name: str) -> ExternalComponent | Callable[..., SafeHtml] | None:
        if name in self.internal_components:
            return self.internal_components[name]

        if name in self.component_registry:
            return self.component_registry[name]

        self._load_auto_imports()
        return self.auto_component_registry.get(name)

    def _load_auto_imports(self) -> None:
        if self.imports_loaded:
            return

        for import_node in self.module.imports:
            module_renderer = self._load_imported_module(import_node.module)
            for name in import_node.names:
                if name not in module_renderer.internal_components:
                    raise RendererError(
                        f"Imported component '{name}' was not found in module '{import_node.module}'"
                    )
                self.auto_component_registry[name] = self._bind_imported_component(module_renderer, name)

        self.imports_loaded = True

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

    @staticmethod
    def _bind_imported_component(
        renderer: "DjuleRenderer",
        component_name: str,
    ) -> Callable[..., SafeHtml]:
        def render_imported_component(**props: object) -> SafeHtml:
            return renderer._render_component_by_name(component_name, dict(props))

        return render_imported_component

    def _render_resolved_component(
        self,
        component_name: str,
        component: ExternalComponent | Callable[..., SafeHtml],
        props: dict[str, object],
    ) -> SafeHtml:
        self._validate_component_props(component_name, component, props)

        if isinstance(component, ComponentDef):
            return self._render_component_def(component, props)

        result = component(**props)
        if isinstance(result, SafeHtml):
            return result
        return SafeHtml(str(result))

    def _validate_component_props(
        self,
        component_name: str,
        component: ExternalComponent | Callable[..., SafeHtml],
        props: dict[str, object],
    ) -> None:
        if "children" in props and not self._component_accepts_children(component):
            raise RendererError(
                f"Component '{component_name}' received nested content, but it does not declare a 'children' prop"
            )

    @staticmethod
    def _component_accepts_children(component: ExternalComponent | Callable[..., SafeHtml]) -> bool:
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
