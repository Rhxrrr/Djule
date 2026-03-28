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
        """Return a human-readable parser error with source coordinates."""
        return f"{self.message} at line {self.token.line}, column {self.token.column}"


class DjuleParser:
    """Happy-path parser for Djule v1.

    This parser intentionally focuses on the first four example files:
    imports, component definitions, Python statements above `return`, and
    returned markup with HTML/component tags plus `{expr}` interpolation.
    """

    def __init__(self, tokens: list[Token]) -> None:
        """Initialize the parser with a pre-tokenized Djule source stream."""
        self.tokens = tokens
        self.index = 0

    @classmethod
    def from_source(cls, source: str) -> "DjuleParser":
        """Lex raw source text and create a parser over the resulting tokens."""
        return cls(DjuleLexer(source).tokenize())

    @classmethod
    def from_file(cls, path: str | Path) -> "DjuleParser":
        """Create a parser directly from a Djule file on disk."""
        return cls(DjuleLexer.from_file(path).tokenize())

    def parse(self) -> Module:
        """Parse a full Djule module into imports and component definitions.

        The module grammar is intentionally small: top-level imports and
        component `def`s only. Leading and trailing blank lines are ignored,
        but any other unexpected top-level token becomes a parser error.
        """
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
        """Parse a `from ... import ...` statement.

        Relative module prefixes like `...components.ui` are allowed. Module
        alias syntax is intentionally rejected here so `from x as y` produces a
        targeted message directing the user to `import ... as alias` instead.
        """
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
        """Parse an `import module[.path] [as alias]` statement."""
        import_token = self._consume(TokenType.IMPORT, "Expected 'import'")
        module_name = self._parse_module_reference(allow_empty=False)

        alias = None
        if self._match(TokenType.AS):
            alias = self._consume(TokenType.NAME, "Expected alias name after 'as'").value

        self._consume(TokenType.NEWLINE, "Expected newline after import")
        return ImportModule(module=module_name, alias=alias, line=import_token.line, column=import_token.column)

    def _parse_module_reference(self, *, allow_empty: bool) -> str:
        """Parse a dotted module path, optionally with leading relative dots.

        When `allow_empty` is true, a purely relative path like `...` is
        accepted. Otherwise at least one name segment must follow the dots.
        """
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
        """Parse one Djule component definition and its return markup.

        A component body may contain Python-like statements above `return`.
        After the `return (...)` markup is parsed, the parser requires the
        component's indentation block to close with a matching `DEDENT`.
        """
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
        """Parse statements until the given terminator token is reached."""
        statements = []
        self._skip_newlines()
        while not self._check(end_type) and not self._check(TokenType.EOF):
            statements.append(self._parse_statement())
            self._skip_newlines()
        return statements

    def _parse_statement(self):
        """Dispatch to the correct statement parser for top-level component code.

        The parser prefers structured statements first (`if`, `for`,
        assignments) and falls back to a plain expression statement otherwise.
        """
        if self._check(TokenType.IF):
            return self._parse_if_stmt()
        if self._check(TokenType.FOR):
            return self._parse_for_stmt()
        if self._check(TokenType.NAME) and self._check_next(TokenType.EQUALS):
            return self._parse_assign_stmt()
        return self._parse_expr_stmt()

    def _parse_assign_stmt(self) -> AssignStmt:
        """Parse an assignment in component code above `return`.

        The right-hand side may be Djule markup or a Python expression. The
        statement must terminate with a newline; multiline grouping is handled
        by token collection rather than by this method directly.
        """
        target = self._consume(TokenType.NAME, "Expected assignment target").value
        self._consume(TokenType.EQUALS, "Expected '=' in assignment")

        if self._check(TokenType.HTML_TAG_OPEN) or self._check(TokenType.COMPONENT_TAG_OPEN):
            value = self._parse_markup_node()
        else:
            value = self._parse_python_expr_until(TokenType.NEWLINE)

        self._consume(TokenType.NEWLINE, "Expected newline after assignment")
        return AssignStmt(target=target, value=value)

    def _parse_if_stmt(self) -> IfStmt:
        """Parse a top-level `if` statement in component code.

        Both the `if` body and optional `else` body are indentation-delimited.
        Missing colons, newlines, or indented bodies surface as parser errors
        tied to the token where the expected structure broke.
        """
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
        """Parse a top-level `for ... in ...:` loop.

        Only a simple name is accepted as the loop target in v1. If tokens
        appear where `in` should be, a ranged parser error is produced so IDEs
        can underline the full invalid target.
        """
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
        """Parse a standalone Python expression statement in component code."""
        expr = self._parse_python_expr_until(TokenType.NEWLINE)
        self._consume(TokenType.NEWLINE, "Expected newline after expression")
        return ExprStmt(value=expr)

    def _parse_block_statements(self) -> list:
        """Parse a normal indented statement block until its closing dedent."""
        statements = []
        self._skip_newlines()
        while not self._check(TokenType.DEDENT) and not self._check(TokenType.EOF):
            statements.append(self._parse_statement())
            self._skip_newlines()
        self._consume(TokenType.DEDENT, "Expected end of indented block")
        return statements

    def _parse_return_stmt(self) -> ReturnStmt:
        """Parse the required `return (...)` markup form for a component."""
        self._consume(TokenType.RETURN, "Expected 'return'")
        self._consume(TokenType.LPAREN, "Expected '(' after return")
        self._skip_newlines()
        value = self._parse_markup_node()
        self._skip_newlines()
        self._consume(TokenType.RPAREN, "Expected ')' after returned markup")
        self._consume(TokenType.NEWLINE, "Expected newline after return")
        return ReturnStmt(value=value)

    def _parse_markup_node(self) -> MarkupNode:
        """Parse the next markup-level node.

        Markup can be an HTML element, a component tag, raw text, or a braced
        Djule expression/block. Legacy single-token `EXPR` nodes are still
        supported as a compatibility path while the tokenized brace form exists.
        """
        if self._check(TokenType.HTML_TAG_OPEN):
            return self._parse_element_node()
        if self._check(TokenType.COMPONENT_TAG_OPEN):
            return self._parse_component_node()
        if self._check(TokenType.LBRACE):
            return self._parse_braced_markup_node()
        if self._check(TokenType.TEXT):
            return TextNode(value=self._advance().value)
        if self._check(TokenType.EXPR):
            token = self._advance()
            return self._parse_legacy_expr_token(token)
        raise self._error("Expected markup node")

    def _parse_element_node(self) -> ElementNode:
        """Parse a plain HTML-like element and all of its child markup."""
        open_token = self._consume(TokenType.HTML_TAG_OPEN, "Expected HTML opening tag")
        attributes = self._parse_attributes()
        self._consume(TokenType.TAG_END, "Expected '>' after opening tag")
        children = self._parse_children_until(TokenType.HTML_TAG_CLOSE, open_token.value)
        self._consume(TokenType.HTML_TAG_CLOSE, f"Expected closing tag </{open_token.value}>")
        self._consume(TokenType.TAG_END, "Expected '>' after closing tag")
        return ElementNode(tag=open_token.value, attributes=attributes, children=children)

    def _parse_component_node(self) -> ComponentNode:
        """Parse a component tag and its nested children.

        The `children` prop name is reserved because nested content is passed
        separately, so using it as an explicit attribute is rejected here.
        """
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
        """Parse a sequence of tag attributes.

        Attributes may take a literal string value or a braced Python
        expression. The legacy single-token `EXPR` form is also accepted so
        older cached/tokenized inputs still parse cleanly.
        """
        attributes = []
        while self._check(TokenType.ATTR_NAME):
            name = self._advance().value
            self._consume(TokenType.EQUALS, "Expected '=' after attribute name")
            if self._check(TokenType.STRING):
                value: str | PythonExpr = self._advance().value
            elif self._check(TokenType.LBRACE):
                value = self._parse_braced_python_expr()
            elif self._check(TokenType.EXPR):
                token = self._advance()
                value = PythonExpr(source=token.value, line=token.line, column=token.column)
            else:
                raise self._error("Expected string or {expr} attribute value")
            attributes.append(AttributeNode(name=name, value=value))
        return attributes

    def _parse_braced_markup_node(self) -> MarkupNode:
        """Parse `{...}` inside markup as either an expression or embedded block.

        The collected inner tokens are converted back to source so the parser
        can decide whether the content is a plain Python expression or a Djule
        block such as `if`, `for`, or an embedded assignment block.
        Malformed block-shaped content gets a targeted parser error.
        """
        open_token = self._consume(TokenType.LBRACE, "Expected '{' before embedded expression")
        inner_tokens = self._collect_tokens_until({TokenType.RBRACE})
        self._consume(TokenType.RBRACE, "Expected '}' after embedded expression")

        if not inner_tokens:
            raise ParserError(message="Expected Python expression inside '{...}'", token=open_token)

        source = self._tokens_to_source(inner_tokens)
        first_token = self._first_meaningful_token(inner_tokens) or open_token

        if self._is_embedded_block_source(source):
            return self._parse_embedded_block_tokens(inner_tokens, first_token)
        if self._looks_like_malformed_embedded_block(source):
            raise ParserError(
                message="Expected embedded block to start with 'if', 'for', or an assignment",
                token=first_token,
            )

        self._validate_python_expression(source, first_token, "Invalid Python expression inside '{...}'")
        return ExpressionNode(source=source, line=open_token.line, column=open_token.column)

    def _parse_braced_python_expr(self) -> PythonExpr:
        """Parse `{...}` where only a Python expression is valid, such as attributes."""
        open_token = self._consume(TokenType.LBRACE, "Expected '{' before attribute expression")
        inner_tokens = self._collect_tokens_until({TokenType.RBRACE})
        self._consume(TokenType.RBRACE, "Expected '}' after attribute expression")

        if not inner_tokens:
            raise ParserError(message="Expected Python expression inside '{...}'", token=open_token)

        first_token = self._first_meaningful_token(inner_tokens) or open_token
        source = self._tokens_to_source(inner_tokens)
        self._validate_python_expression(source, first_token, "Invalid Python expression inside '{...}'")
        return PythonExpr(source=source, line=open_token.line, column=open_token.column)

    def _parse_legacy_expr_token(self, token: Token) -> MarkupNode:
        """Parse the older single-token embedded-expression representation.

        This exists to keep older lexer output and cached data compatible while
        the preferred tokenized brace representation is used elsewhere.
        """
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

    def _parse_embedded_block_tokens(self, tokens: list[Token], origin: Token) -> BlockNode:
        """Parse tokenized embedded block content with a nested parser.

        Leading and trailing blank-line tokens are normalized away before the
        nested parse. Lexer errors from recursive parsing are wrapped as parser
        errors at the outer block origin.
        """
        normalized_tokens = self._normalize_embedded_block_tokens(tokens)
        parser = DjuleParser(
            normalized_tokens + [Token(type=TokenType.EOF, value="", line=origin.line, column=origin.column)]
        )
        try:
            return parser.parse_embedded_block()
        except ParserError:
            raise
        except LexerError as exc:
            raise ParserError(message=exc.message, token=origin) from exc

    def _parse_children_until(self, close_type: TokenType, close_name: str) -> list[MarkupNode]:
        """Parse child markup until the matching closing tag token is reached."""
        children = []
        while not (self._check(close_type) and self._peek().value == close_name):
            if self._check(TokenType.EOF):
                raise self._error(f"Expected closing tag </{close_name}>")
            children.append(self._parse_markup_node())
        return children

    def _parse_python_expr_until(self, stop_type: TokenType) -> PythonExpr:
        """Collect tokens until a stop token and validate them as a Python expression.

        Nested parentheses, brackets, and braces are tracked so delimiters
        inside the expression do not terminate collection too early.
        """
        tokens = self._collect_tokens_until({stop_type})
        if not tokens:
            raise self._error("Expected Python expression")
        source = self._tokens_to_source(tokens)
        self._validate_python_expression(source, tokens[0], "Invalid Python expression")
        return PythonExpr(source=source, line=tokens[0].line, column=tokens[0].column)

    def _parse_embedded_block_source(self, source: str, origin: Token) -> BlockNode:
        """Parse legacy embedded block source text through a nested parser.

        The source is indentation-normalized first. Any nested parser or lexer
        error is remapped back onto the original outer file coordinates.
        """
        normalized_source = self._normalize_embedded_block_source(source)
        try:
            parser = DjuleParser.from_source(normalized_source)
            return parser.parse_embedded_block()
        except ParserError as exc:
            raise self._remap_embedded_parser_error(source, origin, exc) from exc
        except LexerError as exc:
            raise self._remap_embedded_lexer_error(source, origin, exc) from exc

    def parse_embedded_block(self) -> BlockNode:
        """Parse a Djule embedded block body from the current token stream."""
        self._skip_newlines()
        statements = self._parse_block_items_until({TokenType.EOF})
        self._consume(TokenType.EOF, "Expected end of embedded block")
        return BlockNode(statements=statements)

    def _parse_block_items_until(self, stop_types: set[TokenType]) -> list[BlockItem]:
        """Parse embedded block items until one of the stop token types appears."""
        items: list[BlockItem] = []
        self._skip_newlines()
        while not self._check_any(stop_types) and not self._check(TokenType.EOF):
            items.append(self._parse_block_item())
            self._skip_newlines()
        return items

    def _parse_block_item(self) -> BlockItem:
        """Dispatch to the correct embedded-block item parser."""
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
        """Parse an assignment inside an embedded `{...}` block."""
        target = self._consume(TokenType.NAME, "Expected assignment target").value
        self._consume(TokenType.EQUALS, "Expected '=' in assignment")

        if self._starts_markup_node():
            value = self._parse_markup_node()
        else:
            value = self._parse_python_expr_until(TokenType.NEWLINE)

        self._consume(TokenType.NEWLINE, "Expected newline after embedded assignment")
        return EmbeddedAssignNode(target=target, value=value)

    def _parse_embedded_if_node(self) -> EmbeddedIfNode:
        """Parse an embedded `if` / `else` block inside markup braces."""
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
        """Parse an embedded `for ... in ...:` block inside markup braces."""
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
        """Parse a bare expression line inside an embedded block.

        Embedded expression lines are renderable output nodes, unlike top-level
        component expressions which remain ordinary statements.
        """
        expr = self._parse_python_expr_until(TokenType.NEWLINE)
        self._consume(TokenType.NEWLINE, "Expected newline after embedded expression")
        return EmbeddedExprNode(source=expr.source, line=expr.line, column=expr.column)

    def _parse_embedded_block_items(self) -> list[BlockItem]:
        """Parse one indented embedded-block body and consume its closing dedent."""
        items = self._parse_block_items_until({TokenType.DEDENT})
        self._consume(TokenType.DEDENT, "Expected end of embedded block")
        return items

    def _collect_tokens_until(self, stop_types: set[TokenType]) -> list[Token]:
        """Collect tokens until an un-nested stop token is reached.

        Stop tokens inside parentheses, brackets, or braces are ignored so
        expression collection works for nested calls, lists, dicts, and grouped
        expressions without premature termination.
        """
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
        """Reconstruct source text from a token slice.

        This is used for Python expression validation and embedded block
        detection. Structural indentation tokens are skipped, while spacing is
        heuristically restored around punctuation and operators.
        """
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
            if token.type == TokenType.NEWLINE:
                parts.append("\n")
                previous = None
                continue

            if token.type in {TokenType.INDENT, TokenType.DEDENT}:
                continue

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
        """Return whether the current token can begin a markup node."""
        return self._check_any(
            {
                TokenType.HTML_TAG_OPEN,
                TokenType.COMPONENT_TAG_OPEN,
                TokenType.TEXT,
                TokenType.EXPR,
                TokenType.LBRACE,
            }
        )

    @staticmethod
    def _first_meaningful_token(tokens: list[Token]) -> Token | None:
        """Return the first non-whitespace structural token from a token slice."""
        for token in tokens:
            if token.type not in {TokenType.NEWLINE, TokenType.INDENT, TokenType.DEDENT}:
                return token
        return None

    @staticmethod
    def _normalize_embedded_block_tokens(tokens: list[Token]) -> list[Token]:
        """Trim leading and trailing blank-line tokens around embedded blocks."""
        normalized = list(tokens)

        while normalized and normalized[0].type == TokenType.NEWLINE:
            normalized.pop(0)

        while normalized and normalized[-1].type == TokenType.NEWLINE:
            normalized.pop()

        return normalized

    @staticmethod
    def _is_embedded_block_source(source: str) -> bool:
        """Return whether source text looks like a Djule embedded block.

        V1 treats multiline `if`, `for`, and assignment-shaped bodies as block
        syntax. Single-line expressions are intentionally left as plain Python
        expressions even if they contain keywords.
        """
        stripped = source.strip()
        if "\n" not in stripped:
            return False
        if stripped.startswith("if ") or stripped.startswith("for "):
            return True
        first_line = stripped.splitlines()[0]
        return "=" in first_line and "==" not in first_line and "!=" not in first_line

    @staticmethod
    def _looks_like_malformed_embedded_block(source: str) -> bool:
        """Detect multiline source that resembles a block but starts incorrectly.

        This helps produce a better parser error for content like `else:` or a
        colon-terminated first line that is not a supported embedded block form.
        """
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
        """Validate source with Python's expression parser.

        Djule reuses Python's own syntax rules for expressions instead of
        implementing a second expression grammar. Syntax failures are wrapped as
        `ParserError` instances anchored to the provided token.
        """
        try:
            ast.parse(source.strip(), mode="eval")
        except SyntaxError as exc:
            detail = exc.msg or "invalid syntax"
            raise ParserError(message=f"{message}: {detail}", token=token) from exc

    def _invalid_for_target_error(self, target_token: Token, *, embedded: bool) -> ParserError:
        """Build a ranged error for invalid `for` loop targets.

        The parser advances through the invalid target so editor diagnostics can
        underline the whole problematic span instead of a single token.
        """
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
        """Map nested embedded-block coordinates back to outer source coordinates."""
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
        """Remap a nested embedded parser error onto the outer file location."""
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
        """Remap a nested embedded lexer error onto the outer file location."""
        line, column = cls._embedded_error_location(source, origin, exc.line, exc.column)
        return LexerError(message=exc.message, line=line, column=column)

    @staticmethod
    def _normalize_embedded_block_source(source: str) -> str:
        """Normalize legacy embedded block source indentation before reparsing.

        The first line becomes flush-left and later lines are shifted relative
        to the inferred top-level block indentation so nested parsing sees a
        clean standalone block.
        """
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
        """Infer the top-level indent used inside a legacy embedded block.

        The parser looks for likely top-level block starters such as `if`,
        `for`, `else`, and assignments. If none are found, it falls back to a
        conservative guess based on the minimum observed indent.
        """
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
        """Advance past any consecutive newline tokens."""
        while self._match(TokenType.NEWLINE):
            pass

    def _match(self, token_type: TokenType) -> bool:
        """Consume and return whether the current token matches the given type."""
        if self._check(token_type):
            self._advance()
            return True
        return False

    def _check(self, token_type: TokenType) -> bool:
        """Return whether the current token has the given type."""
        return self._peek().type == token_type

    def _check_any(self, token_types: set[TokenType]) -> bool:
        """Return whether the current token is one of the given types."""
        return self._peek().type in token_types

    def _check_next(self, token_type: TokenType) -> bool:
        """Return whether the next token has the given type."""
        return self._peek(1).type == token_type

    def _consume(self, token_type: TokenType, message: str) -> Token:
        """Consume the current token if it matches, otherwise raise a parser error."""
        if self._check(token_type):
            return self._advance()
        raise self._error(message)

    def _advance(self) -> Token:
        """Return the current token and move forward unless already at EOF."""
        token = self.tokens[self.index]
        if token.type != TokenType.EOF:
            self.index += 1
        return token

    def _peek(self, offset: int = 0) -> Token:
        """Return the token at the current index plus `offset`, clamped at EOF."""
        position = min(self.index + offset, len(self.tokens) - 1)
        return self.tokens[position]

    def _error(self, message: str) -> ParserError:
        """Create a parser error anchored to the current token."""
        return ParserError(message=message, token=self._peek())
