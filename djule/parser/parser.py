from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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
from .lexer import DjuleLexer
from .tokens import Token, TokenType


@dataclass
class ParserError(Exception):
    message: str
    token: Token

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
        imports: list[ImportFrom] = []
        components: list[ComponentDef] = []

        self._skip_newlines()

        while not self._check(TokenType.EOF):
            if self._check(TokenType.FROM):
                imports.append(self._parse_import_from())
            elif self._check(TokenType.DEF):
                components.append(self._parse_component_def())
            else:
                raise self._error("Expected import or component definition")
            self._skip_newlines()

        return Module(imports=imports, components=components)

    def _parse_import_from(self) -> ImportFrom:
        self._consume(TokenType.FROM, "Expected 'from'")
        module_parts = [self._consume(TokenType.NAME, "Expected module name").value]
        while self._match(TokenType.DOT):
            module_parts.append(self._consume(TokenType.NAME, "Expected module name after '.'").value)
        self._consume(TokenType.IMPORT, "Expected 'import'")

        names = [self._consume(TokenType.NAME, "Expected import name").value]
        while self._match(TokenType.COMMA):
            names.append(self._consume(TokenType.NAME, "Expected import name after ','").value)

        self._consume(TokenType.NEWLINE, "Expected newline after import")
        return ImportFrom(module=".".join(module_parts), names=names)

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
        target = self._consume(TokenType.NAME, "Expected loop variable").value
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
            return ExpressionNode(source=self._advance().value)
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
        self._consume(TokenType.TAG_END, "Expected '>' after opening component tag")
        children = self._parse_children_until(TokenType.COMPONENT_TAG_CLOSE, open_token.value)
        self._consume(TokenType.COMPONENT_TAG_CLOSE, f"Expected closing tag </{open_token.value}>")
        self._consume(TokenType.TAG_END, "Expected '>' after closing component tag")
        return ComponentNode(name=open_token.value, attributes=attributes, children=children)

    def _parse_attributes(self) -> list[AttributeNode]:
        attributes = []
        while self._check(TokenType.ATTR_NAME):
            name = self._advance().value
            self._consume(TokenType.EQUALS, "Expected '=' after attribute name")
            if self._check(TokenType.STRING):
                value: str | PythonExpr = self._advance().value
            elif self._check(TokenType.EXPR):
                value = PythonExpr(source=self._advance().value)
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
        return PythonExpr(source=self._tokens_to_source(tokens))

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
