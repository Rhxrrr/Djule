from __future__ import annotations

import ast
from dataclasses import dataclass

from .ast_nodes import (
    AssignStmt,
    AttributeNode,
    BlockItem,
    BlockNode,
    ComponentDef,
    ComponentNode,
    EmbeddedAssignNode,
    EmbeddedExprNode,
    EmbeddedForNode,
    EmbeddedIfNode,
    ElementNode,
    ExprStmt,
    ExpressionNode,
    ForStmt,
    IfStmt,
    MarkupNode,
    Module,
    PythonExpr,
    TextNode,
)


SAFE_BUILTIN_NAMES = {
    "bool",
    "dict",
    "enumerate",
    "int",
    "len",
    "list",
    "max",
    "min",
    "range",
    "set",
    "str",
    "sum",
    "tuple",
}


@dataclass(frozen=True)
class SemanticDiagnostic:
    message: str
    line: int
    column: int
    end_column: int | None = None
    code: str = "semantic.undefined-name"
    severity: str = "error"


class DjuleAnalyzer:
    """Lightweight semantic checks for Djule modules."""

    def __init__(self) -> None:
        self.diagnostics: list[SemanticDiagnostic] = []

    def analyze(self, module: Module) -> list[SemanticDiagnostic]:
        module_names = {component.name for component in module.components}
        import_names: set[str] = set()
        for import_node in module.imports:
            if hasattr(import_node, "names"):
                import_names.update(import_node.names)
            else:
                namespace = import_node.alias or import_node.module
                import_names.add(namespace.split(".")[0])

        base_scope = SAFE_BUILTIN_NAMES | module_names | import_names
        for component in module.components:
            self._analyze_component(component, base_scope)
        return self.diagnostics

    def _analyze_component(self, component: ComponentDef, base_scope: set[str]) -> None:
        scope = set(base_scope)
        scope.update(component.params)
        scope = self._analyze_statements(component.body, scope)
        self._analyze_markup_node(component.return_stmt.value, scope)

    def _analyze_statements(self, statements: list[object], scope: set[str]) -> set[str]:
        current = set(scope)
        for statement in statements:
            if isinstance(statement, AssignStmt):
                self._analyze_assign_value(statement.value, current)
                current.add(statement.target)
            elif isinstance(statement, ExprStmt):
                self._check_python_expr(statement.value, current)
            elif isinstance(statement, IfStmt):
                self._check_python_expr(statement.test, current)
                body_scope = self._analyze_statements(statement.body, set(current))
                else_scope = self._analyze_statements(statement.orelse, set(current))
                current |= (body_scope & else_scope)
            elif isinstance(statement, ForStmt):
                self._check_python_expr(statement.iter, current)
                loop_scope = set(current)
                loop_scope.add(statement.target)
                self._analyze_statements(statement.body, loop_scope)
                current.add(statement.target)
        return current

    def _analyze_assign_value(self, value: object, scope: set[str]) -> None:
        if isinstance(value, PythonExpr):
            self._check_python_expr(value, scope)
        else:
            self._analyze_markup_node(value, scope)

    def _analyze_markup_node(self, node: MarkupNode, scope: set[str]) -> set[str]:
        current = set(scope)

        if isinstance(node, TextNode):
            return current

        if isinstance(node, ExpressionNode):
            self._check_expression_source(node.source, node.line, node.column, current)
            return current

        if isinstance(node, BlockNode):
            return self._analyze_block_items(node.statements, current)

        if isinstance(node, ElementNode):
            current = self._analyze_attributes(node.attributes, current)
            for child in node.children:
                current = self._analyze_markup_node(child, current)
            return current

        if isinstance(node, ComponentNode):
            current = self._analyze_attributes(node.attributes, current)
            for child in node.children:
                current = self._analyze_markup_node(child, current)
            return current

        return current

    def _analyze_attributes(self, attributes: list[AttributeNode], scope: set[str]) -> set[str]:
        current = set(scope)
        for attribute in attributes:
            if isinstance(attribute.value, PythonExpr):
                self._check_python_expr(attribute.value, current)
        return current

    def _analyze_block_items(self, items: list[BlockItem], scope: set[str]) -> set[str]:
        current = set(scope)
        for item in items:
            if isinstance(item, (TextNode, ElementNode, ComponentNode, BlockNode, ExpressionNode)):
                current = self._analyze_markup_node(item, current)
            elif isinstance(item, EmbeddedExprNode):
                self._check_expression_source(item.source, item.line, item.column, current)
            elif isinstance(item, EmbeddedAssignNode):
                self._analyze_assign_value(item.value, current)
                current.add(item.target)
            elif isinstance(item, EmbeddedIfNode):
                self._check_python_expr(item.test, current)
                body_scope = self._analyze_block_items(item.body, set(current))
                else_scope = self._analyze_block_items(item.orelse, set(current))
                current |= (body_scope & else_scope)
            elif isinstance(item, EmbeddedForNode):
                self._check_python_expr(item.iter, current)
                loop_scope = set(current)
                loop_scope.add(item.target)
                self._analyze_block_items(item.body, loop_scope)
                current.add(item.target)
        return current

    def _check_python_expr(self, expr: PythonExpr, scope: set[str]) -> None:
        self._check_expression_source(expr.source, expr.line, expr.column, scope)

    def _check_expression_source(self, source: str, line: int, column: int, scope: set[str]) -> None:
        try:
            tree = ast.parse(source, mode="eval")
        except SyntaxError:
            return

        visitor = _UndefinedNameVisitor(scope)
        visitor.visit(tree)
        for name, rel_line, rel_col in visitor.undefined_names:
            absolute_line = line + rel_line - 1 if line > 0 else rel_line
            if line > 0 and rel_line == 1:
                absolute_column = column + rel_col
            else:
                absolute_column = rel_col + 1
            self.diagnostics.append(
                SemanticDiagnostic(
                    message=f"Name '{name}' is not defined in this scope",
                    line=absolute_line,
                    column=absolute_column,
                    end_column=absolute_column + len(name),
                )
            )


class _UndefinedNameVisitor(ast.NodeVisitor):
    def __init__(self, available_names: set[str]) -> None:
        self.available_names = set(available_names)
        self.scopes: list[set[str]] = [set()]
        self.undefined_names: list[tuple[str, int, int]] = []

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and not self._is_defined(node.id):
            self.undefined_names.append((node.id, getattr(node, "lineno", 1), getattr(node, "col_offset", 0)))

    def visit_Lambda(self, node: ast.Lambda) -> None:
        local_names = {arg.arg for arg in node.args.args}
        local_names.update(arg.arg for arg in node.args.posonlyargs)
        local_names.update(arg.arg for arg in node.args.kwonlyargs)
        if node.args.vararg:
            local_names.add(node.args.vararg.arg)
        if node.args.kwarg:
            local_names.add(node.args.kwarg.arg)

        self.scopes.append(local_names)
        self.visit(node.body)
        self.scopes.pop()

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, [node.elt])

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node.generators, [node.elt])

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.generators, [node.elt])

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, [node.key, node.value])

    def _visit_comprehension(self, generators: list[ast.comprehension], body_nodes: list[ast.AST]) -> None:
        self.scopes.append(set())
        for generator in generators:
            self.visit(generator.iter)
            self._bind_target_names(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        for body_node in body_nodes:
            self.visit(body_node)
        self.scopes.pop()

    def _bind_target_names(self, node: ast.AST) -> None:
        if isinstance(node, ast.Name):
            self.scopes[-1].add(node.id)
            return
        for child in ast.iter_child_nodes(node):
            self._bind_target_names(child)

    def _is_defined(self, name: str) -> bool:
        if name in self.available_names:
            return True
        return any(name in scope for scope in reversed(self.scopes))
