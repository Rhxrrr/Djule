from __future__ import annotations

from .ast_nodes import (
    AssignStmt,
    AttributeNode,
    BlockItem,
    BlockNode,
    ComponentDef,
    ComponentNode,
    CsrfTokenNode,
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


class DjulePrinter:
    """Pretty-printer for the currently supported Djule AST subset.

    This is a debug/validation step, not the final compiler. It lets us verify:
    source -> tokens -> AST -> normalized Djule source
    """

    def print_module(self, module: Module) -> str:
        """Render a whole AST module back into normalized Djule source text."""
        lines: list[str] = []

        for import_node in module.imports:
            lines.append(self._print_import(import_node))

        if module.imports and module.components:
            lines.append("")

        for index, component in enumerate(module.components):
            lines.extend(self._print_component(component))
            if index != len(module.components) - 1:
                lines.append("")

        return "\n".join(lines)

    def _print_import(self, node: ImportFrom | ImportModule) -> str:
        """Render one import node back into Djule source."""
        if isinstance(node, ImportModule):
            if node.alias:
                return f"import {node.module} as {node.alias}"
            return f"import {node.module}"
        names = ", ".join(node.names)
        return f"from {node.module} import {names}"

    def _print_component(self, node: ComponentDef) -> list[str]:
        """Render a component definition, including body statements and return markup."""
        params = ", ".join(node.params)
        lines = [f"def {node.name}({params}):"]

        for statement in node.body:
            lines.extend(self._print_statement(statement, indent=1))

        lines.extend(self._print_return(node.return_stmt, indent=1))
        return lines

    def _print_statement(self, statement, indent: int) -> list[str]:
        """Render one top-level component statement with the requested indentation."""
        prefix = "    " * indent

        if isinstance(statement, AssignStmt):
            if isinstance(statement.value, PythonExpr):
                return [f"{prefix}{statement.target} = {statement.value.source}"]
            return [f"{prefix}{statement.target} = {self._print_inline_markup(statement.value)}"]

        if isinstance(statement, ExprStmt):
            return [f"{prefix}{statement.value.source}"]

        if isinstance(statement, IfStmt):
            lines = [f"{prefix}if {statement.test.source}:"]
            for child in statement.body:
                lines.extend(self._print_statement(child, indent + 1))
            if statement.orelse:
                lines.append(f"{prefix}else:")
                for child in statement.orelse:
                    lines.extend(self._print_statement(child, indent + 1))
            return lines

        if isinstance(statement, ForStmt):
            lines = [f"{prefix}for {statement.target} in {statement.iter.source}:"]
            for child in statement.body:
                lines.extend(self._print_statement(child, indent + 1))
            return lines

        raise TypeError(f"Unsupported statement node: {type(statement)!r}")

    def _print_return(self, statement: ReturnStmt, indent: int) -> list[str]:
        """Render the `return (...)` portion of a component."""
        prefix = "    " * indent
        lines = [f"{prefix}return ("]
        lines.extend(self._print_markup_block(statement.value, indent + 1))
        lines.append(f"{prefix})")
        return lines

    def _print_markup_block(self, node: MarkupNode, indent: int) -> list[str]:
        """Render one markup node as one or more source lines."""
        prefix = "    " * indent

        if isinstance(node, FragmentNode):
            lines: list[str] = []
            for child in node.children:
                lines.extend(self._print_markup_block(child, indent))
            return lines

        if isinstance(node, DeclarationNode):
            return [f"{prefix}{node.value}"]

        if isinstance(node, CsrfTokenNode):
            return [f"{prefix}{{% csrf_token %}}"]

        if isinstance(node, TextNode):
            return [f"{prefix}{node.value}"]

        if isinstance(node, ExpressionNode):
            return [f"{prefix}{{{node.source}}}"]

        if isinstance(node, BlockNode):
            return self._print_embedded_block(node, indent)

        if isinstance(node, ElementNode):
            return self._print_tag_block(node.tag, node.attributes, node.children, node.self_closing, indent)

        if isinstance(node, ComponentNode):
            return self._print_tag_block(node.name, node.attributes, node.children, node.self_closing, indent)

        raise TypeError(f"Unsupported markup node: {type(node)!r}")

    def _print_embedded_block(self, node: BlockNode, indent: int) -> list[str]:
        """Render an embedded Djule `{...}` block with nested indentation."""
        prefix = "    " * indent
        lines = [f"{prefix}{{"]
        for statement in node.statements:
            lines.extend(self._print_block_item(statement, indent + 1))
        lines.append(f"{prefix}}}")
        return lines

    def _print_tag_block(
        self,
        name: str,
        attributes: list[AttributeNode],
        children: list[MarkupNode],
        self_closing: bool,
        indent: int,
    ) -> list[str]:
        """Render either an HTML tag or component tag block.

        Children are printed inline when they are all simple text or
        interpolations. Otherwise the printer expands them across multiple lines.
        """
        prefix = "    " * indent
        open_tag = self._format_open_tag(name, attributes)

        if self_closing:
            return [f"{prefix}{open_tag[:-1]} />"]

        if not children:
            return [f"{prefix}{open_tag}</{name}>"]

        if self._is_inline_children(children):
            child_source = "".join(self._print_inline_markup(child) for child in children)
            return [f"{prefix}{open_tag}{child_source}</{name}>"]

        lines = [f"{prefix}{open_tag}"]
        for child in children:
            lines.extend(self._print_markup_block(child, indent + 1))
        lines.append(f"{prefix}</{name}>")
        return lines

    def _format_open_tag(self, name: str, attributes: list[AttributeNode]) -> str:
        """Render an opening tag with any attributes already attached."""
        if not attributes:
            return f"<{name}>"
        rendered_attributes = " ".join(self._print_attribute(attribute) for attribute in attributes)
        return f"<{name} {rendered_attributes}>"

    def _print_attribute(self, attribute: AttributeNode) -> str:
        """Render one attribute value in literal or `{expr}` form."""
        if isinstance(attribute.value, PythonExpr):
            return f"{attribute.name}={{{attribute.value.source}}}"
        return f"{attribute.name}={attribute.value}"

    def _print_inline_markup(self, node: MarkupNode) -> str:
        """Render markup that is legal to print on a single source line.

        Embedded blocks are rejected here because they require explicit
        indentation and brace lines in the output.
        """
        if isinstance(node, FragmentNode):
            return "".join(self._print_inline_markup(child) for child in node.children)
        if isinstance(node, DeclarationNode):
            return node.value
        if isinstance(node, CsrfTokenNode):
            return "{% csrf_token %}"
        if isinstance(node, TextNode):
            return node.value
        if isinstance(node, ExpressionNode):
            return f"{{{node.source}}}"
        if isinstance(node, BlockNode):
            raise TypeError("Embedded blocks cannot be printed inline")
        if isinstance(node, ElementNode):
            if node.self_closing:
                return f"{self._format_open_tag(node.tag, node.attributes)[:-1]} />"
            open_tag = self._format_open_tag(node.tag, node.attributes)
            children = "".join(self._print_inline_markup(child) for child in node.children)
            return f"{open_tag}{children}</{node.tag}>"
        if isinstance(node, ComponentNode):
            if node.self_closing:
                return f"{self._format_open_tag(node.name, node.attributes)[:-1]} />"
            open_tag = self._format_open_tag(node.name, node.attributes)
            children = "".join(self._print_inline_markup(child) for child in node.children)
            return f"{open_tag}{children}</{node.name}>"
        raise TypeError(f"Unsupported markup node: {type(node)!r}")

    def _print_block_item(self, item: BlockItem, indent: int) -> list[str]:
        """Render one item inside an embedded Djule block."""
        prefix = "    " * indent

        if isinstance(item, (FragmentNode, DeclarationNode, CsrfTokenNode, TextNode, ExpressionNode, ElementNode, ComponentNode, BlockNode)):
            return self._print_markup_block(item, indent)

        if isinstance(item, EmbeddedExprNode):
            return [f"{prefix}{item.source}"]

        if isinstance(item, EmbeddedAssignNode):
            if isinstance(item.value, PythonExpr):
                return [f"{prefix}{item.target} = {item.value.source}"]
            return [f"{prefix}{item.target} = {self._print_inline_markup(item.value)}"]

        if isinstance(item, EmbeddedIfNode):
            lines = [f"{prefix}if {item.test.source}:"]
            for child in item.body:
                lines.extend(self._print_block_item(child, indent + 1))
            if item.orelse:
                lines.append(f"{prefix}else:")
                for child in item.orelse:
                    lines.extend(self._print_block_item(child, indent + 1))
            return lines

        if isinstance(item, EmbeddedForNode):
            lines = [f"{prefix}for {item.target} in {item.iter.source}:"]
            for child in item.body:
                lines.extend(self._print_block_item(child, indent + 1))
            return lines

        raise TypeError(f"Unsupported embedded block item: {type(item)!r}")

    @staticmethod
    def _is_inline_children(children: list[MarkupNode]) -> bool:
        """Return whether child markup can be rendered inline without losing structure."""
        return all(isinstance(child, (DeclarationNode, CsrfTokenNode, TextNode, ExpressionNode)) for child in children)
