from __future__ import annotations

import ast
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .lexer import LexerError
from .parser import DjuleParser, ParserError
from .ast_nodes import (
    AssignStmt,
    AttributeNode,
    BlockItem,
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
    ImportFrom,
    ImportModule,
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
VIRTUAL_IMPORT_MODULES = {"builtins"}


@dataclass(frozen=True)
class SemanticDiagnostic:
    """A semantic problem discovered after parsing, formatted for CLI/IDE use."""
    message: str
    line: int
    column: int
    end_column: int | None = None
    code: str = "semantic.undefined-name"
    severity: str = "error"


class DjuleAnalyzer:
    """Lightweight semantic checks for Djule modules."""

    def __init__(self) -> None:
        """Initialize analyzer state for one analysis pass."""
        self.diagnostics: list[SemanticDiagnostic] = []
        self.document_path: Path | None = None
        self.imported_module_exports: dict[Path, set[str] | None] = {}
        self.search_paths: list[Path] = []

    def analyze(
        self,
        module: Module,
        *,
        document_path: str | Path | None = None,
        search_paths: list[str | Path] | None = None,
        global_names: list[str] | None = None,
    ) -> list[SemanticDiagnostic]:
        """Run semantic checks for imports, names, and component references.

        The analyzer is intentionally lightweight. It assumes parsing already
        succeeded and focuses on undefined names, unresolved imports, and
        component references that cannot be found in the current scope.
        """
        self.document_path = Path(document_path).resolve() if document_path else None
        self.search_paths = [
            Path(path).resolve() for path in (search_paths or self._default_search_paths())
        ]
        self._analyze_imports(module.imports)

        module_names = {component.name for component in module.components}
        import_names: set[str] = set()
        for import_node in module.imports:
            if hasattr(import_node, "names"):
                import_names.update(import_node.names)
            else:
                namespace = import_node.alias or import_node.module
                import_names.add(namespace.split(".")[0])

        base_scope = SAFE_BUILTIN_NAMES | module_names | import_names | set(global_names or [])
        for component in module.components:
            self._analyze_component(component, base_scope)
        return self.diagnostics

    def _analyze_imports(self, imports: list[ImportFrom | ImportModule]) -> None:
        """Report imports whose target modules cannot be resolved from search paths."""
        for import_node in imports:
            resolved_path = self._resolve_import_path(import_node.module)
            if import_node.module in VIRTUAL_IMPORT_MODULES:
                continue
            if resolved_path is not None:
                if isinstance(import_node, ImportFrom):
                    self._analyze_imported_names(import_node, resolved_path)
                continue

            start_column = import_node.column + 5 if isinstance(import_node, ImportFrom) else import_node.column + 7
            self.diagnostics.append(
                SemanticDiagnostic(
                    message=f"Imported module '{import_node.module}' could not be resolved",
                    line=import_node.line or 1,
                    column=start_column,
                    end_column=start_column + len(import_node.module),
                    code="semantic.unresolved-import",
                )
            )

    def _analyze_imported_names(self, import_node: ImportFrom, module_path: Path) -> None:
        """Report imported component names that do not exist in the target module."""
        exported_names = self._module_exported_names(module_path)
        if exported_names is None:
            return

        current_column = import_node.column + len("from ") + len(import_node.module) + len(" import ")
        for index, name in enumerate(import_node.names):
            if name not in exported_names:
                self.diagnostics.append(
                    SemanticDiagnostic(
                        message=f"Imported name '{name}' was not found in module '{import_node.module}'",
                        line=import_node.line or 1,
                        column=current_column,
                        end_column=current_column + len(name),
                        code="semantic.unresolved-import-name",
                    )
                )
            current_column += len(name)
            if index < len(import_node.names) - 1:
                current_column += 2

    def _can_resolve_import(self, module_name: str) -> bool:
        """Return whether an import can be resolved as absolute or relative."""
        return self._resolve_import_path(module_name) is not None or module_name in VIRTUAL_IMPORT_MODULES

    def _resolve_import_path(self, module_name: str) -> Path | None:
        """Return the resolved filesystem path for one Djule import when available."""
        if module_name in VIRTUAL_IMPORT_MODULES:
            return None
        if module_name.startswith("."):
            return self._resolve_relative_import(module_name)
        return self._resolve_absolute_import(module_name)

    def _module_exported_names(self, module_path: Path) -> set[str] | None:
        """Return component names defined by one imported Djule module."""
        resolved_path = module_path.resolve()
        if resolved_path in self.imported_module_exports:
            return self.imported_module_exports[resolved_path]

        try:
            module = DjuleParser.from_file(resolved_path).parse()
        except (LexerError, OSError, ParserError):
            self.imported_module_exports[resolved_path] = None
            return None

        exported_names = {component.name for component in module.components}
        self.imported_module_exports[resolved_path] = exported_names
        return exported_names

    def _resolve_absolute_import(self, module_name: str) -> Path | None:
        """Resolve an absolute Djule module import to a file path if it exists."""
        module_parts = module_name.split(".")
        for base_path in self.search_paths:
            file_candidate = base_path.joinpath(*module_parts).with_suffix(".djule")
            package_candidate = base_path.joinpath(*module_parts, "__init__.djule")
            if file_candidate.exists():
                return file_candidate.resolve()
            if package_candidate.exists():
                return package_candidate.resolve()
        return None

    def _resolve_relative_import(self, module_name: str) -> Path | None:
        """Resolve a relative Djule module import from the current document path."""
        if self.document_path is None:
            return None

        leading_dots = len(module_name) - len(module_name.lstrip("."))
        remainder = module_name[leading_dots:]
        module_parts = remainder.split(".") if remainder else []

        base_path = self.document_path.parent
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
        return None

    @staticmethod
    def _default_search_paths() -> list[Path]:
        """Build the default import roots from `DJULE_PATH` or Python's `sys.path`."""
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

    def _analyze_component(self, component: ComponentDef, base_scope: set[str]) -> None:
        """Analyze one component body and its returned markup with a seeded scope."""
        scope = set(base_scope)
        for name in component.params:
            default_expr = component.defaults.get(name)
            if default_expr is not None:
                self._check_python_expr(default_expr, scope)
            scope.add(name)
        scope = self._analyze_statements(component.body, scope)
        self._analyze_markup_node(component.return_stmt.value, scope)

    def _analyze_statements(self, statements: list[object], scope: set[str]) -> set[str]:
        """Walk top-level component statements and track names that become available.

        Branching statements merge only the names guaranteed to exist in both
        branches, while loop targets are added to the running scope after the
        loop so later code can reference them consistently with current Djule semantics.
        """
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
        """Analyze the right-hand side of an assignment, whether Python or markup."""
        if isinstance(value, PythonExpr):
            self._check_python_expr(value, scope)
        else:
            self._analyze_markup_node(value, scope)

    def _analyze_markup_node(self, node: MarkupNode, scope: set[str]) -> set[str]:
        """Walk one markup subtree and validate any embedded expressions it contains."""
        current = set(scope)

        if isinstance(node, FragmentNode):
            for child in node.children:
                current = self._analyze_markup_node(child, current)
            return current

        if isinstance(node, DeclarationNode):
            return current

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
            self._check_component_reference(node, current)
            current = self._analyze_attributes(node.attributes, current)
            for child in node.children:
                current = self._analyze_markup_node(child, current)
            return current

        return current

    def _analyze_attributes(self, attributes: list[AttributeNode], scope: set[str]) -> set[str]:
        """Analyze Python-expression attribute values while preserving the current scope."""
        current = set(scope)
        for attribute in attributes:
            if isinstance(attribute.value, PythonExpr):
                self._check_python_expr(attribute.value, current)
        return current

    def _analyze_block_items(self, items: list[BlockItem], scope: set[str]) -> set[str]:
        """Analyze embedded block items and propagate any names they bind."""
        current = set(scope)
        for item in items:
            if isinstance(item, (FragmentNode, DeclarationNode, TextNode, ElementNode, ComponentNode, BlockNode, ExpressionNode)):
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
        """Validate names used by a `PythonExpr` against the current scope."""
        self._check_expression_source(expr.source, expr.line, expr.column, scope)

    def _check_component_reference(self, node: ComponentNode, scope: set[str]) -> None:
        """Report component tags whose root name is not available in scope."""
        root_name = node.name.split(".")[0]
        if root_name in scope:
            return

        start_column = node.column + 1 if node.column > 0 else 1
        end_column = start_column + len(node.name)
        self.diagnostics.append(
            SemanticDiagnostic(
                message=f"Component reference '{node.name}' is not defined in this scope",
                line=node.line or 1,
                column=start_column,
                end_column=end_column,
                code="semantic.undefined-component",
            )
        )

    def _check_expression_source(self, source: str, line: int, column: int, scope: set[str]) -> None:
        """Parse expression source and report any undefined loaded names.

        Syntax errors are ignored here because the parser already reports them.
        The analyzer only adds semantic diagnostics when a valid expression
        references names that are not available in scope.
        """
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
    """AST visitor that records names loaded before being defined in scope."""
    def __init__(self, available_names: set[str]) -> None:
        """Seed the visitor with globally available names."""
        self.available_names = set(available_names)
        self.scopes: list[set[str]] = [set()]
        self.undefined_names: list[tuple[str, int, int]] = []

    def visit_Name(self, node: ast.Name) -> None:
        """Record undefined loaded names while ignoring stores."""
        if isinstance(node.ctx, ast.Load) and not self._is_defined(node.id):
            self.undefined_names.append((node.id, getattr(node, "lineno", 1), getattr(node, "col_offset", 0)))

    def visit_Lambda(self, node: ast.Lambda) -> None:
        """Create a temporary local scope for lambda parameters."""
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
        """Analyze list comprehensions with their own local binding scope."""
        self._visit_comprehension(node.generators, [node.elt])

    def visit_SetComp(self, node: ast.SetComp) -> None:
        """Analyze set comprehensions with their own local binding scope."""
        self._visit_comprehension(node.generators, [node.elt])

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        """Analyze generator expressions with their own local binding scope."""
        self._visit_comprehension(node.generators, [node.elt])

    def visit_DictComp(self, node: ast.DictComp) -> None:
        """Analyze dict comprehensions with their own local binding scope."""
        self._visit_comprehension(node.generators, [node.key, node.value])

    def _visit_comprehension(self, generators: list[ast.comprehension], body_nodes: list[ast.AST]) -> None:
        """Visit comprehension generators and body nodes in comprehension scope order."""
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
        """Bind every name introduced by a comprehension target pattern."""
        if isinstance(node, ast.Name):
            self.scopes[-1].add(node.id)
            return
        for child in ast.iter_child_nodes(node):
            self._bind_target_names(child)

    def _is_defined(self, name: str) -> bool:
        """Return whether `name` exists in the global or any nested local scope."""
        if name in self.available_names:
            return True
        return any(name in scope for scope in reversed(self.scopes))
