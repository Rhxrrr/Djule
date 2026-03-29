from __future__ import annotations

import ast
import copy
from html import escape

from djule.compiler.render_plan import AttrExprPart, ComponentPlan, ExprPart, NodePart, PlanPart, StaticPart
from djule.compiler.types import ImportedComponentRef, RendererError, SafeHtml
from djule.parser.ast_nodes import (
    AssignStmt,
    AttributeNode,
    BlockNode,
    ComponentDef,
    ComponentNode,
    DeclarationNode,
    ElementNode,
    ExpressionNode,
    FragmentNode,
    MarkupNode,
    PythonExpr,
    TextNode,
)


class DjulePlanMixin:
    """Helpers that compile Djule markup trees into reusable render plans."""
    def _compile_entry_plan(
        self,
        component_name: str,
    ) -> tuple[ComponentPlan, tuple[tuple[str, int, int], ...]]:
        """Compile the persisted entry render plan for one component.

        Dependency paths touched while resolving imports or inlining components
        are tracked so cached plans can be invalidated when any of them change.
        """
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
        """Compile a component plan without persisting dependency metadata."""
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
        """Compile one component with any known prop/local bindings already applied."""
        body_bindings, fully_flattened = self._compile_component_body_bindings(component.body, bindings)
        active_bindings = body_bindings if fully_flattened else bindings
        return self._compile_markup_plan(component.return_stmt.value, active_bindings), not fully_flattened

    def _compile_component_body_bindings(
        self,
        statements: list[object],
        bindings: dict[str, tuple[str, object]],
    ) -> tuple[dict[str, tuple[str, object]], bool]:
        """Try to flatten simple component-body assignments into reusable bindings.

        Only straight-line assignments are flattened. If any non-assignment
        statement appears, the method falls back to runtime execution by
        returning the original bindings and `False`.
        """
        compiled = dict(bindings)

        for statement in statements:
            if not isinstance(statement, AssignStmt):
                return dict(bindings), False

            if isinstance(statement.value, PythonExpr):
                compiled[statement.target] = ("expr", self._rewrite_python_expr(statement.value.source, compiled))
                continue

            if isinstance(
                statement.value,
                (FragmentNode, DeclarationNode, TextNode, ExpressionNode, ElementNode, ComponentNode, BlockNode),
            ):
                compiled[statement.target] = ("plan", self._compile_markup_plan(statement.value, compiled))
                continue

            return dict(bindings), False

        return compiled, True

    def _compile_markup_plan(
        self,
        node: MarkupNode,
        bindings: dict[str, tuple[str, object]],
    ) -> list[PlanPart]:
        """Compile one markup subtree into render-plan parts."""
        if isinstance(node, FragmentNode):
            return self._compile_children_plan(node.children, bindings)

        if isinstance(node, DeclarationNode):
            return [StaticPart(node.value)]

        if isinstance(node, TextNode):
            return [StaticPart(node.value)]

        if isinstance(node, ExpressionNode):
            return self._compile_expression_parts(node.source, bindings, line=node.line, column=node.column)

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
        """Compile a sequence of child markup nodes and merge adjacent static runs."""
        parts: list[PlanPart] = []
        for child in children:
            parts.extend(self._compile_markup_plan(child, bindings))
        return self._merge_static_parts(parts)

    def _compile_expression_parts(
        self,
        source: str,
        bindings: dict[str, tuple[str, object]],
        *,
        line: int = 0,
        column: int = 0,
    ) -> list[PlanPart]:
        """Compile an expression to static output or a runtime expression part.

        Known literal bindings collapse to `StaticPart`, rewritten expressions
        become `ExprPart`, and bound child/plan fragments are spliced directly
        into the surrounding part list.
        """
        binding = self._binding_for_expression(source, bindings)
        if binding is None:
            return [ExprPart(source, line=line, column=column)]

        binding_type, value = binding
        if binding_type == "literal":
            return [StaticPart(str(self._render_expression_value(value)))]
        if binding_type == "expr":
            return [ExprPart(str(value), line=line, column=column)]
        if binding_type in {"children", "plan"}:
            return list(value)
        return [ExprPart(source, line=line, column=column)]

    def _compile_attribute_parts(
        self,
        attribute: AttributeNode,
        bindings: dict[str, tuple[str, object]],
    ) -> list[PlanPart]:
        """Compile one attribute into static or dynamic render-plan parts."""
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
                return [
                    StaticPart(f' {attribute.name}="'),
                    AttrExprPart(str(value), line=attribute.value.line, column=attribute.value.column),
                    StaticPart('"'),
                ]

        return [
            StaticPart(f' {attribute.name}="'),
            AttrExprPart(attribute.value.source, line=attribute.value.line, column=attribute.value.column),
            StaticPart('"'),
        ]

    def _compile_component_node_parts(
        self,
        node: ComponentNode,
        bindings: dict[str, tuple[str, object]],
    ) -> list[PlanPart]:
        """Compile a component usage to static HTML, inlined plan parts, or a runtime node.

        Fully static imported/local components can render immediately. Otherwise
        the compiler tries to inline their plan, falling back to `NodePart`
        when runtime behavior is still required.
        """
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
        """Build binding metadata for component props and nested children."""
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
        """Materialize real prop values when every binding is statically renderable."""
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
        component,
        bindings: dict[str, tuple[str, object]],
    ) -> list[PlanPart] | None:
        """Try to inline another component's compiled plan into the current one."""
        if isinstance(component, ImportedComponentRef):
            resolved = component.renderer._resolve_component(component.component_name)
            if isinstance(resolved, ComponentDef):
                parts, requires_runtime_body = component.renderer._compile_component_with_bindings(resolved, bindings)
                if not requires_runtime_body and not self._plan_parts_require_component_scope(parts):
                    return parts
            return None

        if isinstance(component, ComponentDef):
            parts, requires_runtime_body = self._compile_component_with_bindings(component, bindings)
            if not requires_runtime_body and not self._plan_parts_require_component_scope(parts):
                return parts

        return None

    @staticmethod
    def _plan_parts_require_component_scope(parts: list[PlanPart]) -> bool:
        """Return whether inlining would lose a child component's runtime scope.

        `NodePart` values keep raw AST nodes for later rendering. Those nodes
        evaluate expressions against the environment present at render time, so
        inlining them into a parent component would make child props resolve
        against the wrong scope.
        """
        return any(isinstance(part, NodePart) for part in parts)

    @staticmethod
    def _binding_for_expression(
        source: str,
        bindings: dict[str, tuple[str, object]],
    ) -> tuple[str, object] | None:
        """Return a binding only for simple identifier expressions in v1."""
        if source.isidentifier():
            return bindings.get(source)
        return None

    @classmethod
    def _rewrite_python_expr(
        cls,
        source: str,
        bindings: dict[str, tuple[str, object]],
    ) -> str:
        """Rewrite identifier references inside an expression using known bindings.

        Literal and expression bindings are substituted through Python's AST so
        simple helper assignments can be folded into later dynamic expressions.
        If parsing or unparsing fails, the original source is preserved.
        """
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
        """Record that the current compiled plan depends on the given source path."""
        if self._plan_dependency_paths is not None:
            self._plan_dependency_paths.add(path.resolve())

    @staticmethod
    def _merge_static_parts(parts: list[PlanPart]) -> list[PlanPart]:
        """Coalesce adjacent static render-plan parts into larger chunks."""
        merged: list[PlanPart] = []
        for part in parts:
            if merged and isinstance(merged[-1], StaticPart) and isinstance(part, StaticPart):
                merged[-1] = StaticPart(merged[-1].value + part.value)
            else:
                merged.append(part)
        return merged
