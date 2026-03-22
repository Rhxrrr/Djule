from __future__ import annotations

import ast
from html import escape
from typing import Mapping

from src.compiler.render_plan import AttrExprPart, ComponentPlan, ExprPart, NodePart, PlanPart, StaticPart
from src.compiler.types import ImportedComponentRef, RendererError, SafeHtml
from src.parser.ast_nodes import (
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
    PythonExpr,
    ReturnStmt,
    TextNode,
)


class DjuleRenderMixin:
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
        except Exception as exc:  # pragma: no cover
            raise RendererError(f"Failed to evaluate expression '{source}': {exc}") from exc
