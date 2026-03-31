from __future__ import annotations

import ast
from html import escape
from typing import Mapping

from djule.compiler.render_plan import AttrExprPart, ComponentPlan, ExprPart, NodePart, PlanPart, StaticPart
from djule.compiler.types import ImportedComponentRef, RendererError, SafeHtml
from djule.parser.ast_nodes import (
    AssignStmt,
    AttributeNode,
    BlockItem,
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
    IfStmt,
    MarkupNode,
    PythonExpr,
    ReturnStmt,
    TextNode,
)


class DjuleRenderMixin:
    """Runtime rendering helpers used by `DjuleRenderer`."""
    def render(
        self,
        component_name: str | None = None,
        props: Mapping[str, object] | None = None,
        ambient_props: Mapping[str, object] | None = None,
    ) -> str:
        """Render one component to its final HTML string output."""
        target_name = component_name or self._default_component_name()
        self._ambient_props_stack.append(dict(ambient_props or {}))
        try:
            return str(self._render_component_by_name(target_name, dict(props or {}), persist_plan=True))
        finally:
            self._ambient_props_stack.pop()

    def _render_with_ambient(
        self,
        component_name: str,
        props: dict[str, object],
        *,
        ambient_props: Mapping[str, object] | None = None,
        persist_plan: bool = False,
    ) -> SafeHtml:
        """Render one component while inheriting the current ambient globals."""
        self._ambient_props_stack.append(dict(ambient_props or {}))
        try:
            return self._render_component_by_name(component_name, props, persist_plan=persist_plan)
        finally:
            self._ambient_props_stack.pop()

    def _current_ambient_props(self) -> dict[str, object]:
        """Return the ambient globals visible to the active render stack."""
        if not self._ambient_props_stack:
            return {}
        return self._ambient_props_stack[-1]

    def _get_component_plan(self, component_name: str, *, persist: bool) -> ComponentPlan | None:
        """Return a compiled component plan, using caches when persistence is allowed."""
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
        """Choose the default component to render when none is specified explicitly."""
        if "Page" in self.internal_components:
            return "Page"
        if not self.module.components:
            raise RendererError("Module has no components to render")
        return self.module.components[0].name

    def _format_render_error(
        self,
        message: str,
        *,
        component_name: str | None = None,
        line: int = 0,
        column: int = 0,
        source_path: str | None = None,
    ) -> str:
        """Format a runtime error with file/component/location details first."""
        context_parts = []
        effective_source_path = source_path or (str(self.module_path) if self.module_path is not None else None)
        if effective_source_path is not None:
            context_parts.append(f"file '{effective_source_path}'")
        active_component_name = component_name or self._current_component_name
        if active_component_name:
            context_parts.append(f"component '{active_component_name}'")
        if line and column:
            context_parts.append(f"line {line}, column {column}")
        if context_parts:
            return f"{', '.join(context_parts)}: {message}"
        return message

    def _render_component_by_name(
        self,
        component_name: str,
        props: dict[str, object],
        *,
        persist_plan: bool = False,
    ) -> SafeHtml:
        """Resolve and render a component by name, preserving render context for errors."""
        previous_component_name = self._current_component_name
        self._current_component_name = component_name
        try:
            component = self._resolve_component(component_name)
            if component is None:
                raise RendererError(
                    self._format_render_error(
                        f"Unknown component '{component_name}'",
                        component_name=component_name,
                    )
                )

            self._validate_component_props(component_name, component, props)

            if isinstance(component, ComponentDef):
                return self._render_component_def(component, props, persist_plan=persist_plan)

            if isinstance(component, ImportedComponentRef):
                return component.renderer._render_with_ambient(
                    component.component_name,
                    props,
                    ambient_props=self._current_ambient_props(),
                    persist_plan=persist_plan,
                )

            result = component(**props)
            if isinstance(result, SafeHtml):
                return result
            return SafeHtml(str(result))
        finally:
            self._current_component_name = previous_component_name

    def _render_component_def(
        self,
        component: ComponentDef,
        props: dict[str, object],
        *,
        persist_plan: bool = False,
    ) -> SafeHtml:
        """Render a Djule-defined component with prop validation and optional plan reuse."""
        env = self._module_import_values()
        env.update(self._current_ambient_props())
        env.update(props)

        if "children" in env and "children" not in component.params:
            raise RendererError(
                self._format_render_error(
                    "Received nested content, but no 'children' prop is declared",
                    component_name=component.name,
                )
            )

        if "children" in component.params and "children" not in env:
            env["children"] = SafeHtml("")

        missing = [name for name in component.params if name not in env]
        if missing:
            missing_args = ", ".join(missing)
            raise RendererError(
                self._format_render_error(
                    f"Missing prop(s): {missing_args}",
                    component_name=component.name,
                )
            )

        component_plan = self._get_component_plan(component.name, persist=persist_plan)
        if component_plan is None:
            self._execute_statements(component.body, env)
            return self._render_return(component.return_stmt, env)
        if component_plan.requires_runtime_body:
            self._execute_statements(component.body, env)
        return self._render_component_plan(component_plan, env)

    def _execute_statements(self, statements: list[object], env: dict[str, object]) -> None:
        """Execute component-body statements sequentially in the given environment."""
        for statement in statements:
            self._execute_statement(statement, env)

    def _execute_statement(self, statement: object, env: dict[str, object]) -> None:
        """Execute one top-level component statement and mutate the environment as needed."""
        if isinstance(statement, AssignStmt):
            if isinstance(statement.value, PythonExpr):
                env[statement.target] = self._eval_python_expr(
                    statement.value.source,
                    env,
                    line=statement.value.line,
                    column=statement.value.column,
                )
            else:
                env[statement.target] = self._render_markup_node(statement.value, env)
            return

        if isinstance(statement, ExprStmt):
            self._eval_python_expr(statement.value.source, env, line=statement.value.line, column=statement.value.column)
            return

        if isinstance(statement, IfStmt):
            branch = (
                statement.body
                if self._eval_python_expr(statement.test.source, env, line=statement.test.line, column=statement.test.column)
                else statement.orelse
            )
            self._execute_statements(branch, env)
            return

        if isinstance(statement, ForStmt):
            iterable = self._eval_python_expr(statement.iter.source, env, line=statement.iter.line, column=statement.iter.column)
            for item in iterable:
                env[statement.target] = item
                self._execute_statements(statement.body, env)
            return

        raise RendererError(f"Unsupported statement type: {type(statement)!r}")

    def _render_return(self, statement: ReturnStmt, env: dict[str, object]) -> SafeHtml:
        """Render the markup returned by a component once its body has executed."""
        return self._render_markup_node(statement.value, env)

    def _render_component_plan(self, component_plan: ComponentPlan, env: dict[str, object]) -> SafeHtml:
        """Execute a compiled component plan into one safe HTML fragment."""
        fragments: list[str] = []
        for part in component_plan.parts:
            fragments.append(self._render_plan_part(part, env))
        return SafeHtml("".join(fragments))

    def _render_plan_part(self, part: PlanPart, env: dict[str, object]) -> str:
        """Render one compiled plan part according to its static or dynamic type."""
        if isinstance(part, StaticPart):
            return part.value

        if isinstance(part, ExprPart):
            return str(
                self._render_expression_value(
                    self._eval_python_expr(
                        part.source,
                        env,
                        line=part.line,
                        column=part.column,
                        source_path=part.source_path,
                        component_name=part.component_name,
                    )
                )
            )

        if isinstance(part, AttrExprPart):
            value = self._eval_python_expr(
                part.source,
                env,
                line=part.line,
                column=part.column,
                source_path=part.source_path,
                component_name=part.component_name,
            )
            if value is None:
                return ""
            return escape(str(value), quote=True)

        if isinstance(part, NodePart):
            return str(self._render_markup_node(part.node, env))

        raise RendererError(f"Unsupported render plan part: {type(part)!r}")

    def _render_markup_node(self, node: MarkupNode, env: dict[str, object]) -> SafeHtml:
        """Render one markup AST node into safe HTML."""
        if isinstance(node, FragmentNode):
            return self._render_children(node.children, env)

        if isinstance(node, DeclarationNode):
            return SafeHtml(node.value)

        if isinstance(node, TextNode):
            return SafeHtml(node.value)

        if isinstance(node, ExpressionNode):
            return self._render_expression_value(
                self._eval_python_expr(node.source, env, line=node.line, column=node.column)
            )

        if isinstance(node, BlockNode):
            return self._render_block_node(node, env)

        if isinstance(node, ElementNode):
            return self._render_element_node(node, env)

        if isinstance(node, ComponentNode):
            return self._render_component_node(node, env)

        raise RendererError(f"Unsupported markup node: {type(node)!r}")

    def _render_block_node(self, node: BlockNode, env: dict[str, object]) -> SafeHtml:
        """Render an embedded Djule block by appending emitted fragments in order."""
        fragments: list[str] = []
        self._execute_block_items(node.statements, env, fragments)
        return SafeHtml("".join(fragments))

    def _render_element_node(self, node: ElementNode, env: dict[str, object]) -> SafeHtml:
        """Render a plain HTML element with rendered attributes and children."""
        rendered_attributes = self._render_attributes(node.attributes, env)
        if node.self_closing:
            return SafeHtml(f"<{node.tag}{rendered_attributes} />")
        rendered_children = self._render_children(node.children, env)
        return SafeHtml(f"<{node.tag}{rendered_attributes}>{rendered_children}</{node.tag}>")

    def _render_component_node(self, node: ComponentNode, env: dict[str, object]) -> SafeHtml:
        """Render a component tag after resolving props and nested children."""
        props = self._resolve_props(node.attributes, env)
        component = self._resolve_component(node.name)
        if component is None:
            raise RendererError(
                self._format_render_error(
                    f"Unknown component '{node.name}'",
                    component_name=node.name,
                    line=node.line,
                    column=node.column,
                )
            )

        if node.children:
            if not self._component_accepts_children(component):
                raise RendererError(
                    self._format_render_error(
                        "Received nested content, but no 'children' prop is declared",
                        component_name=node.name,
                        line=node.line,
                        column=node.column,
                    )
                )
            props["children"] = self._render_children(node.children, env)
        return self._render_resolved_component(node.name, component, props)

    def _render_children(self, children: list[MarkupNode], env: dict[str, object]) -> SafeHtml:
        """Render child markup nodes and concatenate their HTML safely."""
        if not children:
            return SafeHtml("")
        return SafeHtml("".join(str(self._render_markup_node(child, env)) for child in children))

    def _render_attributes(self, attributes: list[AttributeNode], env: dict[str, object]) -> str:
        """Render HTML attributes with proper escaping for dynamic values."""
        parts = []
        for attribute in attributes:
            value = self._resolve_attribute_value(attribute, env)
            parts.append(f' {attribute.name}="{escape(value, quote=True)}"')
        return "".join(parts)

    def _resolve_props(self, attributes: list[AttributeNode], env: dict[str, object]) -> dict[str, object]:
        """Evaluate component prop values into a plain Python props dictionary."""
        props: dict[str, object] = {}
        for attribute in attributes:
            if isinstance(attribute.value, PythonExpr):
                props[attribute.name] = self._eval_python_expr(
                    attribute.value.source,
                    env,
                    line=attribute.value.line,
                    column=attribute.value.column,
                )
            else:
                props[attribute.name] = ast.literal_eval(attribute.value)
        return props

    def _resolve_attribute_value(self, attribute: AttributeNode, env: dict[str, object]) -> str:
        """Resolve one HTML attribute value to its final string form."""
        if isinstance(attribute.value, PythonExpr):
            value = self._eval_python_expr(
                attribute.value.source,
                env,
                line=attribute.value.line,
                column=attribute.value.column,
            )
        else:
            value = ast.literal_eval(attribute.value)

        if value is None:
            return ""
        return str(value)

    def _render_expression_value(self, value: object) -> SafeHtml:
        """Convert an expression result into safe HTML output.

        `SafeHtml` passes through untouched, `None` becomes an empty string,
        sequences are concatenated item by item, and everything else is escaped.
        """
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
        """Execute each embedded block item, appending any rendered output fragments."""
        for item in items:
            self._execute_block_item(item, env, fragments)

    def _execute_block_item(
        self,
        item: BlockItem,
        env: dict[str, object],
        fragments: list[str],
    ) -> None:
        """Execute one embedded block item and emit or bind values as needed."""
        if isinstance(item, (FragmentNode, DeclarationNode, TextNode, ExpressionNode, ElementNode, ComponentNode, BlockNode)):
            fragments.append(str(self._render_markup_node(item, env)))
            return

        if isinstance(item, EmbeddedExprNode):
            fragments.append(
                str(
                    self._render_expression_value(
                        self._eval_python_expr(item.source, env, line=item.line, column=item.column)
                    )
                )
            )
            return

        if isinstance(item, EmbeddedAssignNode):
            if isinstance(item.value, PythonExpr):
                env[item.target] = self._eval_python_expr(
                    item.value.source,
                    env,
                    line=item.value.line,
                    column=item.value.column,
                )
            else:
                env[item.target] = self._render_markup_node(item.value, env)
            return

        if isinstance(item, EmbeddedIfNode):
            branch = (
                item.body
                if self._eval_python_expr(item.test.source, env, line=item.test.line, column=item.test.column)
                else item.orelse
            )
            self._execute_block_items(branch, env, fragments)
            return

        if isinstance(item, EmbeddedForNode):
            iterable = self._eval_python_expr(item.iter.source, env, line=item.iter.line, column=item.iter.column)
            for value in iterable:
                env[item.target] = value
                self._execute_block_items(item.body, env, fragments)
            return

        raise RendererError(f"Unsupported embedded block item: {type(item)!r}")

    def _eval_python_expr(
        self,
        source: str,
        env: dict[str, object],
        *,
        line: int = 0,
        column: int = 0,
        source_path: str | None = None,
        component_name: str | None = None,
    ) -> object:
        """Evaluate a Python expression in the current Djule environment.

        Expressions are compiled once per unique source string and cached.
        Runtime failures are wrapped with file, component, and source position
        context so editor and CLI diagnostics can show useful locations.
        """
        scope = {"__builtins__": self.builtins, **env}
        try:
            filename = source_path or (str(self.module_path) if self.module_path is not None else "<djule>")
            cache_key = (filename, source)
            code = self._compiled_expr_cache.get(cache_key)
            if code is None:
                code = compile(source, filename, "eval")
                self._compiled_expr_cache[cache_key] = code
            return eval(code, scope, scope)
        except Exception as exc:  # pragma: no cover
            raise RendererError(
                self._format_render_error(
                    f"Failed to evaluate expression '{source}': {exc}",
                    line=line,
                    column=column,
                    component_name=component_name,
                    source_path=source_path,
                )
            ) from exc
