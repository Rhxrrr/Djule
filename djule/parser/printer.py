from __future__ import annotations

from .ast_nodes import (
    AssignStmt,
    AttributeNode,
    ComponentDef,
    ComponentNode,
    ElementNode,
    ExprStmt,
    ExpressionNode,
    ForStmt,
    IfStmt,
    ImportFrom,
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

    def _print_import(self, node: ImportFrom) -> str:
        names = ", ".join(node.names)
        return f"from {node.module} import {names}"

    def _print_component(self, node: ComponentDef) -> list[str]:
        params = ", ".join(node.params)
        lines = [f"def {node.name}({params}):"]

        for statement in node.body:
            lines.extend(self._print_statement(statement, indent=1))

        lines.extend(self._print_return(node.return_stmt, indent=1))
        return lines

    def _print_statement(self, statement, indent: int) -> list[str]:
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
        prefix = "    " * indent
        lines = [f"{prefix}return ("]
        lines.extend(self._print_markup_block(statement.value, indent + 1))
        lines.append(f"{prefix})")
        return lines

    def _print_markup_block(self, node: MarkupNode, indent: int) -> list[str]:
        prefix = "    " * indent

        if isinstance(node, TextNode):
            return [f"{prefix}{node.value}"]

        if isinstance(node, ExpressionNode):
            return [f"{prefix}{{{node.source}}}"]

        if isinstance(node, ElementNode):
            return self._print_tag_block(node.tag, node.attributes, node.children, indent)

        if isinstance(node, ComponentNode):
            return self._print_tag_block(node.name, node.attributes, node.children, indent)

        raise TypeError(f"Unsupported markup node: {type(node)!r}")

    def _print_tag_block(
        self,
        name: str,
        attributes: list[AttributeNode],
        children: list[MarkupNode],
        indent: int,
    ) -> list[str]:
        prefix = "    " * indent
        open_tag = self._format_open_tag(name, attributes)

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
        if not attributes:
            return f"<{name}>"
        rendered_attributes = " ".join(self._print_attribute(attribute) for attribute in attributes)
        return f"<{name} {rendered_attributes}>"

    def _print_attribute(self, attribute: AttributeNode) -> str:
        if isinstance(attribute.value, PythonExpr):
            return f"{attribute.name}={{{attribute.value.source}}}"
        return f"{attribute.name}={attribute.value}"

    def _print_inline_markup(self, node: MarkupNode) -> str:
        if isinstance(node, TextNode):
            return node.value
        if isinstance(node, ExpressionNode):
            return f"{{{node.source}}}"
        if isinstance(node, ElementNode):
            open_tag = self._format_open_tag(node.tag, node.attributes)
            children = "".join(self._print_inline_markup(child) for child in node.children)
            return f"{open_tag}{children}</{node.tag}>"
        if isinstance(node, ComponentNode):
            open_tag = self._format_open_tag(node.name, node.attributes)
            children = "".join(self._print_inline_markup(child) for child in node.children)
            return f"{open_tag}{children}</{node.name}>"
        raise TypeError(f"Unsupported markup node: {type(node)!r}")

    @staticmethod
    def _is_inline_children(children: list[MarkupNode]) -> bool:
        return all(isinstance(child, (TextNode, ExpressionNode)) for child in children)
