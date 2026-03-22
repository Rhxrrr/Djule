from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import re

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
    ImportFrom,
    ImportModule,
    MarkupNode,
    Module,
    PythonExpr,
    ReturnStmt,
    TextNode,
)
from .lexer import DjuleLexer
from .lexer import LexerError
from .tokens import Token, TokenType


@dataclass
class ParserError(Exception):
    message: str
    token: Token
    end_column: int | None = None

    def __str__(self) -> str:
        return f"{self.message} at line {self.token.line}, column {self.token.column}"


class DjuleParser:
    """Happy-path parser for Djule v1.

    This parser intentionally focuses on the first four example files:
    imports, component definitions, Python statements above `return`, and
    returned markup with HTML/component tags plus `{expr}` interpolation.
    """

    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.index = 0

    @classmethod
    def from_source(cls, source: str) -> "DjuleParser":
        return cls(DjuleLexer(source).tokenize())

    @classmethod
    def from_file(cls, path: str | Path) -> "DjuleParser":
        return cls(DjuleLexer.from_file(path).tokenize())

    def parse(self) -> Module:
        imports = []
        components: list[ComponentDef] = []

        self._skip_newlines()

        while not self._check(TokenType.EOF):
            if self._check(TokenType.FROM):
                imports.append(self._parse_import_from())
            elif self._check(TokenType.IMPORT):
                imports.append(self._parse_import_module())
            elif self._check(TokenType.DEF):
                components.append(self._parse_component_def())
            else:
                raise self._error("Expected import or component definition")
            self._skip_newlines()

        return Module(imports=imports, components=components)

    def _parse_import_from(self) -> ImportFrom:
        from_token = self._consume(TokenType.FROM, "Expected 'from'")
        module_name = self._parse_module_reference(allow_empty=True)
        if self._check(TokenType.AS):
            raise self._error("Expected 'import' after module path; use 'import ... as <alias>' for module aliases")
        self._consume(TokenType.IMPORT, "Expected 'import'")

        names = [self._consume(TokenType.NAME, "Expected import name").value]
        while self._match(TokenType.COMMA):
            names.append(self._consume(TokenType.NAME, "Expected import name after ','").value)

        self._consume(TokenType.NEWLINE, "Expected newline after import")
        return ImportFrom(module=module_name, names=names, line=from_token.line, column=from_token.column)

    def _parse_import_module(self) -> ImportModule:
        import_token = self._consume(TokenType.IMPORT, "Expected 'import'")
        module_name = self._parse_module_reference(allow_empty=False)

        alias = None
        if self._match(TokenType.AS):
            alias = self._consume(TokenType.NAME, "Expected alias name after 'as'").value

        self._consume(TokenType.NEWLINE, "Expected newline after import")
        return ImportModule(module=module_name, alias=alias, line=import_token.line, column=import_token.column)

    def _parse_module_reference(self, *, allow_empty: bool) -> str:
        relative_level = 0
        while self._match(TokenType.DOT):
            relative_level += 1

        module_parts: list[str] = []
        if self._check(TokenType.NAME):
            module_parts.append(self._consume(TokenType.NAME, "Expected module name").value)
            while self._match(TokenType.DOT):
                module_parts.append(self._consume(TokenType.NAME, "Expected module name after '.'").value)
        elif relative_level == 0 or not allow_empty:
            raise self._error("Expected module name")

        module_name = "." * relative_level
        if module_parts:
            module_name += ".".join(module_parts)
        return module_name

    def _parse_component_def(self) -> ComponentDef:
        self._consume(TokenType.DEF, "Expected 'def'")
        name = self._consume(TokenType.NAME, "Expected component name after def").value
        self._consume(TokenType.LPAREN, "Expected '(' after component name")

        params: list[str] = []
        if not self._check(TokenType.RPAREN):
            params.append(self._consume(TokenType.NAME, "Expected parameter name").value)
            while self._match(TokenType.COMMA):
                params.append(self._consume(TokenType.NAME, "Expected parameter name after ','").value)

        self._consume(TokenType.RPAREN, "Expected ')' after parameters")
        self._consume(TokenType.COLON, "Expected ':' after component signature")
        self._consume(TokenType.NEWLINE, "Expected newline after component signature")
        self._consume(TokenType.INDENT, "Expected indented component body")

        body = self._parse_statements_until(TokenType.RETURN)
        return_stmt = self._parse_return_stmt()

        self._skip_newlines()
        self._consume(TokenType.DEDENT, "Expected end of component body")
        return ComponentDef(name=name, params=params, body=body, return_stmt=return_stmt)

    def _parse_statements_until(self, end_type: TokenType) -> list:
        statements = []
        self._skip_newlines()
        while not self._check(end_type) and not self._check(TokenType.EOF):
            statements.append(self._parse_statement())
            self._skip_newlines()
        return statements

    def _parse_statement(self):
        if self._check(TokenType.IF):
            return self._parse_if_stmt()
        if self._check(TokenType.FOR):
            return self._parse_for_stmt()
        if self._check(TokenType.NAME) and self._check_next(TokenType.EQUALS):
            return self._parse_assign_stmt()
        return self._parse_expr_stmt()

    def _parse_assign_stmt(self) -> AssignStmt:
        target = self._consume(TokenType.NAME, "Expected assignment target").value
        self._consume(TokenType.EQUALS, "Expected '=' in assignment")

        if self._check(TokenType.HTML_TAG_OPEN) or self._check(TokenType.COMPONENT_TAG_OPEN):
            value = self._parse_markup_node()
        else:
            value = self._parse_python_expr_until(TokenType.NEWLINE)

        self._consume(TokenType.NEWLINE, "Expected newline after assignment")
        return AssignStmt(target=target, value=value)

    def _parse_if_stmt(self) -> IfStmt:
        self._consume(TokenType.IF, "Expected 'if'")
        test = self._parse_python_expr_until(TokenType.COLON)
        self._consume(TokenType.COLON, "Expected ':' after if condition")
        self._consume(TokenType.NEWLINE, "Expected newline after if condition")
        self._consume(TokenType.INDENT, "Expected indented if body")

        body = self._parse_block_statements()

        orelse = []
        self._skip_newlines()
        if self._match(TokenType.ELSE):
            self._consume(TokenType.COLON, "Expected ':' after else")
            self._consume(TokenType.NEWLINE, "Expected newline after else")
            self._consume(TokenType.INDENT, "Expected indented else body")
            orelse = self._parse_block_statements()

        return IfStmt(test=test, body=body, orelse=orelse)

    def _parse_for_stmt(self) -> ForStmt:
        self._consume(TokenType.FOR, "Expected 'for'")
        target_token = self._consume(TokenType.NAME, "Expected loop variable")
        target = target_token.value
        if not self._check(TokenType.IN):
            raise self._invalid_for_target_error(target_token, embedded=False)
        self._consume(TokenType.IN, "Expected 'in' in for loop")
        iter_expr = self._parse_python_expr_until(TokenType.COLON)
        self._consume(TokenType.COLON, "Expected ':' after for loop")
        self._consume(TokenType.NEWLINE, "Expected newline after for loop")
        self._consume(TokenType.INDENT, "Expected indented for body")

        body = self._parse_block_statements()
        return ForStmt(target=target, iter=iter_expr, body=body)

    def _parse_expr_stmt(self) -> ExprStmt:
        expr = self._parse_python_expr_until(TokenType.NEWLINE)
        self._consume(TokenType.NEWLINE, "Expected newline after expression")
        return ExprStmt(value=expr)

    def _parse_block_statements(self) -> list:
        statements = []
        self._skip_newlines()
        while not self._check(TokenType.DEDENT) and not self._check(TokenType.EOF):
            statements.append(self._parse_statement())
            self._skip_newlines()
        self._consume(TokenType.DEDENT, "Expected end of indented block")
        return statements

    def _parse_return_stmt(self) -> ReturnStmt:
        self._consume(TokenType.RETURN, "Expected 'return'")
        self._consume(TokenType.LPAREN, "Expected '(' after return")
        self._skip_newlines()
        value = self._parse_markup_node()
        self._skip_newlines()
        self._consume(TokenType.RPAREN, "Expected ')' after returned markup")
        self._consume(TokenType.NEWLINE, "Expected newline after return")
        return ReturnStmt(value=value)

    def _parse_markup_node(self) -> MarkupNode:
        if self._check(TokenType.HTML_TAG_OPEN):
            return self._parse_element_node()
        if self._check(TokenType.COMPONENT_TAG_OPEN):
            return self._parse_component_node()
        if self._check(TokenType.TEXT):
            return TextNode(value=self._advance().value)
        if self._check(TokenType.EXPR):
            token = self._advance()
            source = token.value
            if self._is_embedded_block_source(source):
                return self._parse_embedded_block_source(source, token)
            if self._looks_like_malformed_embedded_block(source):
                line, column = self._embedded_error_location(source, token, 1, 1)
                raise ParserError(
                    message="Expected embedded block to start with 'if', 'for', or an assignment",
                    token=Token(type=token.type, value=token.value, line=line, column=column),
                )
            self._validate_python_expression(source, token, "Invalid Python expression inside '{...}'")
            return ExpressionNode(source=source, line=token.line, column=token.column)
        raise self._error("Expected markup node")

    def _parse_element_node(self) -> ElementNode:
        open_token = self._consume(TokenType.HTML_TAG_OPEN, "Expected HTML opening tag")
        attributes = self._parse_attributes()
        self._consume(TokenType.TAG_END, "Expected '>' after opening tag")
        children = self._parse_children_until(TokenType.HTML_TAG_CLOSE, open_token.value)
        self._consume(TokenType.HTML_TAG_CLOSE, f"Expected closing tag </{open_token.value}>")
        self._consume(TokenType.TAG_END, "Expected '>' after closing tag")
        return ElementNode(tag=open_token.value, attributes=attributes, children=children)

    def _parse_component_node(self) -> ComponentNode:
        open_token = self._consume(TokenType.COMPONENT_TAG_OPEN, "Expected component opening tag")
        attributes = self._parse_attributes()
        for attribute in attributes:
            if attribute.name == "children":
                raise self._error(
                    "The 'children' prop is reserved for nested component content; use content between the tags instead"
                )
        self._consume(TokenType.TAG_END, "Expected '>' after opening component tag")
        children = self._parse_children_until(TokenType.COMPONENT_TAG_CLOSE, open_token.value)
        self._consume(TokenType.COMPONENT_TAG_CLOSE, f"Expected closing tag </{open_token.value}>")
        self._consume(TokenType.TAG_END, "Expected '>' after closing component tag")
        return ComponentNode(
            name=open_token.value,
            attributes=attributes,
            children=children,
            line=open_token.line,
            column=open_token.column,
        )

    def _parse_attributes(self) -> list[AttributeNode]:
        attributes = []
        while self._check(TokenType.ATTR_NAME):
            name = self._advance().value
            self._consume(TokenType.EQUALS, "Expected '=' after attribute name")
            if self._check(TokenType.STRING):
                value: str | PythonExpr = self._advance().value
            elif self._check(TokenType.EXPR):
                token = self._advance()
                value = PythonExpr(source=token.value, line=token.line, column=token.column)
            else:
                raise self._error("Expected string or {expr} attribute value")
            attributes.append(AttributeNode(name=name, value=value))
        return attributes

    def _parse_children_until(self, close_type: TokenType, close_name: str) -> list[MarkupNode]:
        children = []
        while not (self._check(close_type) and self._peek().value == close_name):
            if self._check(TokenType.EOF):
                raise self._error(f"Expected closing tag </{close_name}>")
            children.append(self._parse_markup_node())
        return children

    def _parse_python_expr_until(self, stop_type: TokenType) -> PythonExpr:
        tokens = self._collect_tokens_until({stop_type})
        if not tokens:
            raise self._error("Expected Python expression")
        source = self._tokens_to_source(tokens)
        self._validate_python_expression(source, tokens[0], "Invalid Python expression")
        return PythonExpr(source=source, line=tokens[0].line, column=tokens[0].column)

    def _parse_embedded_block_source(self, source: str, origin: Token) -> BlockNode:
        normalized_source = self._normalize_embedded_block_source(source)
        try:
            parser = DjuleParser.from_source(normalized_source)
            return parser.parse_embedded_block()
        except ParserError as exc:
            raise self._remap_embedded_parser_error(source, origin, exc) from exc
        except LexerError as exc:
            raise self._remap_embedded_lexer_error(source, origin, exc) from exc

    def parse_embedded_block(self) -> BlockNode:
        self._skip_newlines()
        statements = self._parse_block_items_until({TokenType.EOF})
        self._consume(TokenType.EOF, "Expected end of embedded block")
        return BlockNode(statements=statements)

    def _parse_block_items_until(self, stop_types: set[TokenType]) -> list[BlockItem]:
        items: list[BlockItem] = []
        self._skip_newlines()
        while not self._check_any(stop_types) and not self._check(TokenType.EOF):
            items.append(self._parse_block_item())
            self._skip_newlines()
        return items

    def _parse_block_item(self) -> BlockItem:
        if self._check(TokenType.IF):
            return self._parse_embedded_if_node()
        if self._check(TokenType.FOR):
            return self._parse_embedded_for_node()
        if self._check(TokenType.NAME) and self._check_next(TokenType.EQUALS):
            return self._parse_embedded_assign_node()
        if self._starts_markup_node():
            return self._parse_markup_node()
        return self._parse_embedded_expr_node()

    def _parse_embedded_assign_node(self) -> EmbeddedAssignNode:
        target = self._consume(TokenType.NAME, "Expected assignment target").value
        self._consume(TokenType.EQUALS, "Expected '=' in assignment")

        if self._starts_markup_node():
            value = self._parse_markup_node()
        else:
            value = self._parse_python_expr_until(TokenType.NEWLINE)

        self._consume(TokenType.NEWLINE, "Expected newline after embedded assignment")
        return EmbeddedAssignNode(target=target, value=value)

    def _parse_embedded_if_node(self) -> EmbeddedIfNode:
        self._consume(TokenType.IF, "Expected 'if'")
        test = self._parse_python_expr_until(TokenType.COLON)
        self._consume(TokenType.COLON, "Expected ':' after if condition")
        self._consume(TokenType.NEWLINE, "Expected newline after if condition")
        self._consume(TokenType.INDENT, "Expected indented embedded if body")
        body = self._parse_embedded_block_items()

        orelse: list[BlockItem] = []
        self._skip_newlines()
        if self._match(TokenType.ELSE):
            self._consume(TokenType.COLON, "Expected ':' after else")
            self._consume(TokenType.NEWLINE, "Expected newline after else")
            self._consume(TokenType.INDENT, "Expected indented embedded else body")
            orelse = self._parse_embedded_block_items()

        return EmbeddedIfNode(test=test, body=body, orelse=orelse)

    def _parse_embedded_for_node(self) -> EmbeddedForNode:
        self._consume(TokenType.FOR, "Expected 'for'")
        target_token = self._consume(TokenType.NAME, "Expected loop variable")
        target = target_token.value
        if not self._check(TokenType.IN):
            raise self._invalid_for_target_error(target_token, embedded=True)
        self._consume(TokenType.IN, "Expected 'in' in embedded for loop")
        iter_expr = self._parse_python_expr_until(TokenType.COLON)
        self._consume(TokenType.COLON, "Expected ':' after embedded for loop")
        self._consume(TokenType.NEWLINE, "Expected newline after embedded for loop")
        self._consume(TokenType.INDENT, "Expected indented embedded for body")
        body = self._parse_embedded_block_items()
        return EmbeddedForNode(target=target, iter=iter_expr, body=body)

    def _parse_embedded_expr_node(self) -> EmbeddedExprNode:
        expr = self._parse_python_expr_until(TokenType.NEWLINE)
        self._consume(TokenType.NEWLINE, "Expected newline after embedded expression")
        return EmbeddedExprNode(source=expr.source, line=expr.line, column=expr.column)

    def _parse_embedded_block_items(self) -> list[BlockItem]:
        items = self._parse_block_items_until({TokenType.DEDENT})
        self._consume(TokenType.DEDENT, "Expected end of embedded block")
        return items

    def _collect_tokens_until(self, stop_types: set[TokenType]) -> list[Token]:
        tokens: list[Token] = []
        paren_depth = 0
        bracket_depth = 0
        brace_depth = 0

        while not self._check(TokenType.EOF):
            current = self._peek()
            if (
                current.type in stop_types
                and paren_depth == 0
                and bracket_depth == 0
                and brace_depth == 0
            ):
                break

            token = self._advance()
            tokens.append(token)

            if token.type == TokenType.LPAREN:
                paren_depth += 1
            elif token.type == TokenType.RPAREN:
                paren_depth = max(0, paren_depth - 1)
            elif token.type == TokenType.LBRACKET:
                bracket_depth += 1
            elif token.type == TokenType.RBRACKET:
                bracket_depth = max(0, bracket_depth - 1)
            elif token.type == TokenType.LBRACE:
                brace_depth += 1
            elif token.type == TokenType.RBRACE:
                brace_depth = max(0, brace_depth - 1)

        return tokens

    @staticmethod
    def _tokens_to_source(tokens: list[Token]) -> str:
        parts: list[str] = []
        no_space_before = {
            TokenType.LPAREN,
            TokenType.LBRACKET,
            TokenType.RPAREN,
            TokenType.RBRACKET,
            TokenType.COMMA,
            TokenType.COLON,
            TokenType.DOT,
        }
        no_space_after = {
            TokenType.LPAREN,
            TokenType.LBRACKET,
            TokenType.DOT,
        }

        previous: Token | None = None
        for token in tokens:
            if not parts:
                parts.append(token.value)
                previous = token
                continue

            need_space = True
            if token.type in no_space_before:
                need_space = False
            if previous and previous.type in no_space_after:
                need_space = False
            if token.type == TokenType.OPERATOR or (previous and previous.type == TokenType.OPERATOR):
                need_space = True

            if need_space:
                parts.append(" ")
            parts.append(token.value)
            previous = token

        return "".join(parts)

    def _starts_markup_node(self) -> bool:
        return self._check_any({TokenType.HTML_TAG_OPEN, TokenType.COMPONENT_TAG_OPEN, TokenType.TEXT, TokenType.EXPR})

    @staticmethod
    def _is_embedded_block_source(source: str) -> bool:
        stripped = source.strip()
        if "\n" not in stripped:
            return False
        if stripped.startswith("if ") or stripped.startswith("for "):
            return True
        first_line = stripped.splitlines()[0]
        return "=" in first_line and "==" not in first_line and "!=" not in first_line

    @staticmethod
    def _looks_like_malformed_embedded_block(source: str) -> bool:
        stripped = source.strip()
        if "\n" not in stripped:
            return False

        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        if not lines:
            return False

        first_line = lines[0]
        if first_line.endswith(":"):
            return True

        return any(line == "else:" or line.startswith("elif ") for line in lines[1:])

    @staticmethod
    def _validate_python_expression(source: str, token: Token, message: str) -> None:
        try:
            ast.parse(source.strip(), mode="eval")
        except SyntaxError as exc:
            detail = exc.msg or "invalid syntax"
            raise ParserError(message=f"{message}: {detail}", token=token) from exc

    def _invalid_for_target_error(self, target_token: Token, *, embedded: bool) -> ParserError:
        invalid_tokens = [target_token]
        while not self._check_any({TokenType.IN, TokenType.COLON, TokenType.NEWLINE, TokenType.EOF}):
            invalid_tokens.append(self._advance())

        last_token = invalid_tokens[-1]
        loop_kind = "embedded for loop" if embedded else "for loop"
        message = f"Expected 'in' after loop variable in {loop_kind}"
        end_column = last_token.column + max(len(last_token.value), 1)
        return ParserError(message=message, token=target_token, end_column=end_column)

    @staticmethod
    def _embedded_error_location(source: str, origin: Token, nested_line: int, nested_column: int) -> tuple[int, int]:
        block_lines = source.splitlines() or [source]
        line_index = max(0, min(len(block_lines) - 1, nested_line - 1))
        actual_line = origin.line + nested_line

        raw_line = block_lines[line_index]
        if line_index == 0:
            actual_column = max(1, origin.column + 4 + max(0, nested_column - 1))
        else:
            leading_spaces = len(raw_line) - len(raw_line.lstrip(" "))
            actual_column = max(1, leading_spaces + max(1, nested_column))
        return actual_line, actual_column

    @classmethod
    def _remap_embedded_parser_error(cls, source: str, origin: Token, exc: ParserError) -> ParserError:
        line, column = cls._embedded_error_location(source, origin, exc.token.line, exc.token.column)
        end_column = None
        if exc.end_column is not None:
            _, end_column = cls._embedded_error_location(source, origin, exc.token.line, exc.end_column)
        return ParserError(
            message=exc.message,
            token=Token(type=exc.token.type, value=exc.token.value, line=line, column=column),
            end_column=end_column,
        )

    @classmethod
    def _remap_embedded_lexer_error(cls, source: str, origin: Token, exc: LexerError) -> LexerError:
        line, column = cls._embedded_error_location(source, origin, exc.line, exc.column)
        return LexerError(message=exc.message, line=line, column=column)

    @staticmethod
    def _normalize_embedded_block_source(source: str) -> str:
        lines = source.strip("\n").splitlines()
        if not lines:
            return ""
        if len(lines) == 1:
            return lines[0].strip()

        first_line = lines[0].strip()
        other_lines = lines[1:]
        base_indent = DjuleParser._infer_embedded_base_indent(other_lines)

        normalized_lines = [first_line]
        for line in other_lines:
            if not line.strip():
                normalized_lines.append("")
                continue

            indent = len(line) - len(line.lstrip(" "))
            adjusted_indent = max(indent - base_indent, 0)
            normalized_lines.append(f"{' ' * adjusted_indent}{line.lstrip(' ')}")

        return "\n".join(normalized_lines)

    @staticmethod
    def _infer_embedded_base_indent(lines: list[str]) -> int:
        indents = [
            len(line) - len(line.lstrip(" "))
            for line in lines
            if line.strip()
        ]
        if not indents:
            return 0

        top_level_pattern = re.compile(r"^(if |for |else:|elif |[A-Za-z_]\w*\s*=)")
        candidate_indents = []
        for line in lines:
            stripped = line.lstrip(" ")
            if not stripped:
                continue
            if top_level_pattern.match(stripped):
                candidate_indents.append(len(line) - len(stripped))

        if candidate_indents:
            return min(candidate_indents)

        return max(min(indents) - 4, 0)

    def _skip_newlines(self) -> None:
        while self._match(TokenType.NEWLINE):
            pass

    def _match(self, token_type: TokenType) -> bool:
        if self._check(token_type):
            self._advance()
            return True
        return False

    def _check(self, token_type: TokenType) -> bool:
        return self._peek().type == token_type

    def _check_any(self, token_types: set[TokenType]) -> bool:
        return self._peek().type in token_types

    def _check_next(self, token_type: TokenType) -> bool:
        return self._peek(1).type == token_type

    def _consume(self, token_type: TokenType, message: str) -> Token:
        if self._check(token_type):
            return self._advance()
        raise self._error(message)

    def _advance(self) -> Token:
        token = self.tokens[self.index]
        if token.type != TokenType.EOF:
            self.index += 1
        return token

    def _peek(self, offset: int = 0) -> Token:
        position = min(self.index + offset, len(self.tokens) - 1)
        return self.tokens[position]

    def _error(self, message: str) -> ParserError:
        return ParserError(message=message, token=self._peek())
