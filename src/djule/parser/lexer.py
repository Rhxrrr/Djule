from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .tokens import Token, TokenType


KEYWORDS = {
    "from": TokenType.FROM,
    "import": TokenType.IMPORT,
    "as": TokenType.AS,
    "def": TokenType.DEF,
    "return": TokenType.RETURN,
    "if": TokenType.IF,
    "else": TokenType.ELSE,
    "for": TokenType.FOR,
    "in": TokenType.IN,
    "not": TokenType.NOT,
}

MULTI_CHAR_OPERATORS = ("+=", "==", "!=", ">=", "<=")
SINGLE_CHAR_OPERATORS = {"+", "-", "*", "/", ">", "<"}
STRING_PREFIX_CHARS = set("fFrRuUbB")


@dataclass
class LexerError(Exception):
    message: str
    line: int
    column: int

    def __str__(self) -> str:
        return f"{self.message} at line {self.line}, column {self.column}"


class DjuleLexer:
    """Tokenizer for the Djule happy-path syntax.

    V1 intentionally keeps expressions as source strings and focuses on the
    subset needed by the first example files.
    """

    def __init__(self, source: str) -> None:
        self.source = source
        self.length = len(source)
        self.index = 0
        self.line = 1
        self.column = 1
        self.at_line_start = True
        self.paren_depth = 0
        self.indent_stack = [0]
        self.tokens: list[Token] = []

    @classmethod
    def from_file(cls, path: str | Path) -> "DjuleLexer":
        return cls(Path(path).read_text())

    def tokenize(self) -> list[Token]:
        while not self.is_at_end():
            if self.at_line_start and self.paren_depth == 0:
                self._handle_indentation()
                if self.is_at_end():
                    break

            ch = self.peek()

            if ch in " \t":
                self.advance()
                continue

            if ch == "#":
                self._skip_comment()
                continue

            if ch == "\n":
                self._emit(TokenType.NEWLINE, "")
                self.advance()
                continue

            if self._starts_string():
                self._lex_string()
                continue

            if self._starts_markup_fragment():
                self._lex_markup_fragment()
                continue

            if ch.isalpha() or ch == "_":
                self._lex_identifier_or_keyword()
                continue

            if ch.isdigit():
                self._lex_number()
                continue

            if self._match_punctuation():
                continue

            if self._match_operator():
                continue

            raise LexerError("Unexpected character", self.line, self.column)

        if self.tokens and self.tokens[-1].type != TokenType.NEWLINE:
            self.tokens.append(Token(TokenType.NEWLINE, "", self.line, self.column))

        while len(self.indent_stack) > 1:
            self.indent_stack.pop()
            self.tokens.append(Token(TokenType.DEDENT, "", self.line, self.column))

        self.tokens.append(Token(TokenType.EOF, "", self.line, self.column))
        return self.tokens

    def _handle_indentation(self) -> None:
        start = self.index
        indent = 0

        while not self.is_at_end() and self.peek() == " ":
            indent += 1
            self.advance()

        if not self.is_at_end() and self.peek() == "\t":
            raise LexerError("Tabs are not supported for indentation", self.line, self.column)

        next_char = self.peek() if not self.is_at_end() else ""

        if next_char in {"", "\n", "#"}:
            # Blank and comment-only lines do not change indentation.
            return

        current_indent = self.indent_stack[-1]
        if indent > current_indent:
            self.indent_stack.append(indent)
            self.tokens.append(Token(TokenType.INDENT, "", self.line, 1))
        elif indent < current_indent:
            while len(self.indent_stack) > 1 and indent < self.indent_stack[-1]:
                self.indent_stack.pop()
                self.tokens.append(Token(TokenType.DEDENT, "", self.line, 1))
            if indent != self.indent_stack[-1]:
                raise LexerError("Inconsistent indentation", self.line, 1)

        # Reset if indentation handling consumed spaces and we need the current
        # token to start at the first non-space character.
        if self.index != start:
            self.at_line_start = False

    def _skip_comment(self) -> None:
        while not self.is_at_end() and self.peek() != "\n":
            self.advance()

    def _lex_identifier_or_keyword(self) -> None:
        line, column = self.line, self.column
        start = self.index
        while not self.is_at_end() and (self.peek().isalnum() or self.peek() == "_"):
            self.advance()
        value = self.source[start:self.index]
        token_type = KEYWORDS.get(value, TokenType.NAME)
        self.tokens.append(Token(token_type, value, line, column))

    def _lex_number(self) -> None:
        line, column = self.line, self.column
        start = self.index
        while not self.is_at_end() and self.peek().isdigit():
            self.advance()
        if not self.is_at_end() and self.peek() == "." and self.peek(1).isdigit():
            self.advance()
            while not self.is_at_end() and self.peek().isdigit():
                self.advance()
        self.tokens.append(Token(TokenType.NUMBER, self.source[start:self.index], line, column))

    def _starts_string(self) -> bool:
        if self.peek() in {"'", '"'}:
            return True

        if self.peek() not in STRING_PREFIX_CHARS:
            return False

        if self.peek(1) in {"'", '"'}:
            return True

        if self.peek(1) in STRING_PREFIX_CHARS and self.peek(2) in {"'", '"'}:
            return True

        return False

    def _lex_string(self) -> None:
        line, column = self.line, self.column
        start = self.index

        if self.peek() in STRING_PREFIX_CHARS and self.peek(1) in {"'", '"'}:
            self.advance()
        elif self.peek() in STRING_PREFIX_CHARS and self.peek(1) in STRING_PREFIX_CHARS and self.peek(2) in {"'", '"'}:
            self.advance()
            self.advance()

        quote = self.peek()
        self.advance()

        escaped = False
        while not self.is_at_end():
            ch = self.advance()
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == quote:
                break
        else:
            raise LexerError("Unterminated string", line, column)

        self.tokens.append(Token(TokenType.STRING, self.source[start:self.index], line, column))

    def _match_punctuation(self) -> bool:
        ch = self.peek()
        mapping = {
            "(": TokenType.LPAREN,
            ")": TokenType.RPAREN,
            "[": TokenType.LBRACKET,
            "]": TokenType.RBRACKET,
            "{": TokenType.LBRACE,
            "}": TokenType.RBRACE,
            ":": TokenType.COLON,
            ",": TokenType.COMMA,
            ".": TokenType.DOT,
            "=": TokenType.EQUALS,
        }
        token_type = mapping.get(ch)
        if token_type is None:
            return False

        self._emit(token_type, ch)
        self.advance()

        if token_type == TokenType.LPAREN:
            self.paren_depth += 1
        elif token_type == TokenType.RPAREN and self.paren_depth > 0:
            self.paren_depth -= 1
        return True

    def _match_operator(self) -> bool:
        for operator in MULTI_CHAR_OPERATORS:
            if self.source.startswith(operator, self.index):
                self._emit(TokenType.OPERATOR, operator)
                self._advance_text(operator)
                return True

        if self.peek() in SINGLE_CHAR_OPERATORS:
            self._emit(TokenType.OPERATOR, self.peek())
            self.advance()
            return True

        return False

    def _starts_markup_fragment(self) -> bool:
        if self.peek() != "<":
            return False

        next_char = self.peek(1)
        if next_char.isalpha():
            return True

        return next_char == "/" and self.peek(2).isalpha()

    def _lex_markup_fragment(self) -> None:
        tag_stack: list[tuple[str, bool]] = []

        while not self.is_at_end():
            if self._starts_markup_fragment():
                name, is_component, is_closing = self._lex_tag()
                if is_closing:
                    if not tag_stack:
                        raise LexerError("Unexpected closing tag", self.line, self.column)
                    expected_name, expected_component = tag_stack.pop()
                    if (name, is_component) != (expected_name, expected_component):
                        raise LexerError(
                            f"Expected closing tag for {expected_name}",
                            self.line,
                            self.column,
                        )
                    if not tag_stack:
                        return
                else:
                    tag_stack.append((name, is_component))
                continue

            if self.peek() == "{":
                self._lex_markup_expression()
                continue

            self._lex_markup_text()

        raise LexerError("Unterminated markup fragment", self.line, self.column)

    def _lex_tag(self) -> tuple[str, bool, bool]:
        line, column = self.line, self.column
        is_closing = self.peek(1) == "/"
        self.advance()  # <
        if is_closing:
            self.advance()  # /

        start = self.index
        while not self.is_at_end() and (self.peek().isalnum() or self.peek() in {"_", "-", "."}):
            self.advance()

        name = self.source[start:self.index]
        if not name:
            raise LexerError("Expected tag name", line, column)

        is_component = "." in name or name[0].isupper()
        token_type = {
            (False, False): TokenType.HTML_TAG_OPEN,
            (False, True): TokenType.COMPONENT_TAG_OPEN,
            (True, False): TokenType.HTML_TAG_CLOSE,
            (True, True): TokenType.COMPONENT_TAG_CLOSE,
        }[(is_closing, is_component)]
        self.tokens.append(Token(token_type, name, line, column))

        if not is_closing:
            self._lex_tag_attributes(name, line, column)

        while not self.is_at_end() and self.peek() in " \t":
            self.advance()

        if self.peek() != ">":
            raise LexerError("Expected > to close tag", self.line, self.column)

        self.tokens.append(Token(TokenType.TAG_END, ">", self.line, self.column))
        self.advance()
        return name, is_component, is_closing

    def _lex_tag_attributes(self, tag_name: str, tag_line: int, tag_column: int) -> None:
        while not self.is_at_end():
            while not self.is_at_end() and self.peek() in " \t\r\n":
                self.advance()

            if self.peek() in {">", ""}:
                return

            if self.peek() in {"<", "/"}:
                raise LexerError(f"Expected > to close tag <{tag_name}>", tag_line, tag_column)

            line, column = self.line, self.column
            start = self.index
            while not self.is_at_end() and (self.peek().isalnum() or self.peek() in {"_", "-", ":"}):
                self.advance()
            name = self.source[start:self.index]
            if not name:
                raise LexerError("Expected attribute name", line, column)
            self.tokens.append(Token(TokenType.ATTR_NAME, name, line, column))

            while not self.is_at_end() and self.peek() in " \t":
                self.advance()

            if self.peek() != "=":
                raise LexerError("Expected = after attribute name", self.line, self.column)
            self.tokens.append(Token(TokenType.EQUALS, "=", self.line, self.column))
            self.advance()

            while not self.is_at_end() and self.peek() in " \t":
                self.advance()

            if self._starts_string():
                self._lex_string()
            elif self.peek() == "{":
                self._lex_markup_expression()
            else:
                raise LexerError("Expected string or {expr} attribute value", self.line, self.column)

    def _lex_markup_expression(self) -> None:
        line, column = self.line, self.column
        self.advance()  # {
        depth = 1
        chars: list[str] = []
        string_quote = ""
        escaped = False

        while not self.is_at_end():
            ch = self.advance()

            if string_quote:
                chars.append(ch)
                if escaped:
                    escaped = False
                    continue
                if ch == "\\":
                    escaped = True
                    continue
                if ch == string_quote:
                    string_quote = ""
                continue

            if ch in {"'", '"'}:
                string_quote = ch
                chars.append(ch)
                continue

            if ch == "{":
                depth += 1
                chars.append(ch)
                continue

            if ch == "}":
                depth -= 1
                if depth == 0:
                    expression = "".join(chars).strip()
                    self.tokens.append(Token(TokenType.EXPR, expression, line, column))
                    return
                chars.append(ch)
                continue

            chars.append(ch)

        raise LexerError("Unterminated markup expression", line, column)

    def _lex_markup_text(self) -> None:
        line, column = self.line, self.column
        start = self.index
        while not self.is_at_end() and self.peek() not in {"<", "{"}:
            self.advance()

        raw_text = self.source[start:self.index]
        normalized = self._normalize_markup_text(raw_text)
        if normalized is not None:
            self.tokens.append(Token(TokenType.TEXT, normalized, line, column))

    @staticmethod
    def _normalize_markup_text(text: str) -> str | None:
        if not text or text.isspace():
            return None

        if "\n" in text:
            significant_lines = [line for line in text.splitlines() if line.strip()]
            pieces = [line.strip() for line in significant_lines]
            if not pieces:
                return None
            normalized = " ".join(pieces)
            if significant_lines[-1].endswith(" "):
                normalized = f"{normalized} "
            return normalized

        return text

    def is_at_end(self) -> bool:
        return self.index >= self.length

    def peek(self, offset: int = 0) -> str:
        position = self.index + offset
        if position >= self.length:
            return ""
        return self.source[position]

    def advance(self) -> str:
        ch = self.source[self.index]
        self.index += 1
        if ch == "\n":
            self.line += 1
            self.column = 1
            self.at_line_start = True
        else:
            self.column += 1
            self.at_line_start = False
        return ch

    def _advance_text(self, text: str) -> None:
        for _ in text:
            self.advance()

    def _emit(self, token_type: TokenType, value: str) -> None:
        self.tokens.append(Token(token_type, value, self.line, self.column))
