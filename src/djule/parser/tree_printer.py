from __future__ import annotations

from .ast_nodes import (
    AssignStmt,
    AttributeNode,
    BlockNode,
    ComponentDef,
    ComponentNode,
    DeclarationNode,
    EmbeddedAssignNode,
    EmbeddedExprNode,
    EmbeddedForNode,
    EmbeddedIfNode,
    ElementNode,
    ExprStmt,
    ExpressionNode,
    ForStmt,
    FragmentNode,
    IfStmt,
    ImportFrom,
    ImportModule,
    MarkupNode,
    Module,
    PythonExpr,
    ReturnStmt,
    TextNode,
)


class DjuleTreePrinter:
    """Render the Djule AST as a readable terminal tree."""

    def print_module(self, module: Module) -> str:
        """Render the root module node and its children as an ASCII tree."""
        lines = ["Module"]
        children: list[tuple[str, object]] = [
            ("imports", module.imports),
            ("components", module.components),
        ]
        self._render_named_children(lines, "", children)
        return "\n".join(lines)

    def _render_named_children(
        self,
        lines: list[str],
        prefix: str,
        children: list[tuple[str, object]],
    ) -> None:
        """Render labeled child groups, skipping empty values."""
        visible = [(label, value) for label, value in children if self._should_render(value)]
        for index, (label, value) in enumerate(visible):
            is_last = index == len(visible) - 1
            branch = self._branch(prefix, is_last, label)
            lines.append(branch)
            child_prefix = self._child_prefix(prefix, is_last)
            self._render_value(lines, child_prefix, value)

    def _render_value(self, lines: list[str], prefix: str, value: object) -> None:
        """Render one tree value, delegating to node or scalar renderers as needed."""
        if isinstance(value, str):
            lines.append(self._branch(prefix, True, f"String: {value}"))
            return

        if isinstance(value, list):
            for index, item in enumerate(value):
                self._render_node(lines, prefix, item, is_last=index == len(value) - 1)
            return

        self._render_node(lines, prefix, value, is_last=True)

    def _render_node(self, lines: list[str], prefix: str, node: object, is_last: bool) -> None:
        """Render one AST node and then recurse into its structured children."""
        label = self._node_label(node)
        lines.append(self._branch(prefix, is_last, label))
        child_prefix = self._child_prefix(prefix, is_last)

        if isinstance(node, (ImportFrom, ImportModule)):
            return

        if isinstance(node, ComponentDef):
            self._render_named_children(
                lines,
                child_prefix,
                [
                    ("body", node.body),
                    ("return", node.return_stmt),
                ],
            )
            return

        if isinstance(node, AssignStmt):
            self._render_named_children(lines, child_prefix, [("value", node.value)])
            return

        if isinstance(node, ExprStmt):
            self._render_named_children(lines, child_prefix, [("value", node.value)])
            return

        if isinstance(node, IfStmt):
            self._render_named_children(
                lines,
                child_prefix,
                [
                    ("test", node.test),
                    ("body", node.body),
                    ("else", node.orelse),
                ],
            )
            return

        if isinstance(node, ForStmt):
            self._render_named_children(
                lines,
                child_prefix,
                [
                    ("iter", node.iter),
                    ("body", node.body),
                ],
            )
            return

        if isinstance(node, ReturnStmt):
            self._render_named_children(lines, child_prefix, [("value", node.value)])
            return

        if isinstance(node, FragmentNode):
            self._render_named_children(lines, child_prefix, [("children", node.children)])
            return

        if isinstance(node, (ElementNode, ComponentNode)):
            children = [("attributes", node.attributes), ("children", node.children)]
            self._render_named_children(lines, child_prefix, children)
            return

        if isinstance(node, BlockNode):
            self._render_named_children(lines, child_prefix, [("statements", node.statements)])
            return

        if isinstance(node, EmbeddedAssignNode):
            self._render_named_children(lines, child_prefix, [("value", node.value)])
            return

        if isinstance(node, EmbeddedIfNode):
            self._render_named_children(
                lines,
                child_prefix,
                [
                    ("test", node.test),
                    ("body", node.body),
                    ("else", node.orelse),
                ],
            )
            return

        if isinstance(node, EmbeddedForNode):
            self._render_named_children(
                lines,
                child_prefix,
                [
                    ("iter", node.iter),
                    ("body", node.body),
                ],
            )
            return

        if isinstance(node, AttributeNode):
            self._render_named_children(lines, child_prefix, [("value", node.value)])
            return

        if isinstance(node, (PythonExpr, DeclarationNode, TextNode, ExpressionNode, EmbeddedExprNode)):
            return

        raise TypeError(f"Unsupported AST node for tree printing: {type(node)!r}")

    @staticmethod
    def _node_label(node: object) -> str:
        """Return the one-line label used for an AST node in the printed tree."""
        if isinstance(node, ImportFrom):
            names = ", ".join(node.names)
            return f"ImportFrom module={node.module} names=[{names}]"
        if isinstance(node, ImportModule):
            if node.alias:
                return f"ImportModule module={node.module} alias={node.alias}"
            return f"ImportModule module={node.module}"
        if isinstance(node, ComponentDef):
            params = ", ".join(
                f"{name}={node.defaults[name].source}" if name in node.defaults else name
                for name in node.params
            )
            return f"ComponentDef name={node.name} params=[{params}]"
        if isinstance(node, AssignStmt):
            return f"AssignStmt target={node.target}"
        if isinstance(node, ExprStmt):
            return "ExprStmt"
        if isinstance(node, IfStmt):
            return "IfStmt"
        if isinstance(node, ForStmt):
            return f"ForStmt target={node.target}"
        if isinstance(node, ReturnStmt):
            return "ReturnStmt"
        if isinstance(node, FragmentNode):
            return "FragmentNode"
        if isinstance(node, ElementNode):
            if node.self_closing:
                return f"ElementNode <{node.tag} />"
            return f"ElementNode <{node.tag}>"
        if isinstance(node, ComponentNode):
            if node.self_closing:
                return f"ComponentNode <{node.name} />"
            return f"ComponentNode <{node.name}>"
        if isinstance(node, BlockNode):
            return "BlockNode"
        if isinstance(node, EmbeddedAssignNode):
            return f"EmbeddedAssignNode target={node.target}"
        if isinstance(node, EmbeddedIfNode):
            return "EmbeddedIfNode"
        if isinstance(node, EmbeddedForNode):
            return f"EmbeddedForNode target={node.target}"
        if isinstance(node, AttributeNode):
            return f"AttributeNode {node.name}"
        if isinstance(node, PythonExpr):
            return f"PythonExpr: {node.source}"
        if isinstance(node, DeclarationNode):
            return f"DeclarationNode: {node.value!r}"
        if isinstance(node, TextNode):
            return f"TextNode: {node.value!r}"
        if isinstance(node, ExpressionNode):
            return f"ExpressionNode: {{{node.source}}}"
        if isinstance(node, EmbeddedExprNode):
            return f"EmbeddedExprNode: {node.source}"
        raise TypeError(f"Unsupported AST node: {type(node)!r}")

    @staticmethod
    def _should_render(value: object) -> bool:
        """Return whether a value should appear in the output tree at all."""
        if isinstance(value, list):
            return bool(value)
        return value is not None

    @staticmethod
    def _branch(prefix: str, is_last: bool, label: str) -> str:
        """Build one tree branch line with the correct connector characters."""
        connector = "└── " if is_last else "├── "
        return f"{prefix}{connector}{label}"

    @staticmethod
    def _child_prefix(prefix: str, is_last: bool) -> str:
        """Return the prefix to use for children of the current branch."""
        return prefix + ("    " if is_last else "│   ")
