from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .tokens import (
    MULTI_CHAR_OPERATORS,
    SINGLE_CHAR_OPERATORS,
    STRING_PREFIX_CHARS,
    Token,
    TokenType,
)


@dataclass
class LexerError(Exception):
    message: str
    line: int
    column: int

    def __str__(self) -> str:
        """Return a human-readable lexer error with source coordinates."""
        return f"{self.message} at line {self.line}, column {self.column}"


class DjuleLexer:
    """Tokenizes Djule source into Python-like and markup tokens.

    Djule treats most embedded Python expressions as source text rather than
    building a full Python expression tree at lex time, which keeps the lexer
    focused on source boundaries and Djule-specific syntax.
    """

    def __init__(self, source: str) -> None:
        """Initialize lexer state for a raw Djule source string.

        The lexer tracks the current source position, block indentation stack,
        and whether the previous significant token means the next real line
        must indent. That lets tokenization catch indentation mistakes early
        without needing parser context.
        """
        self.source = source
        self.length = len(source)
        self.index = 0
        self.line = 1
        self.column = 1
        self.at_line_start = True
        self.paren_depth = 0
        self.indent_stack = [0]
        self._last_significant_token_type: TokenType | None = None
        self._expects_indent = False
        self.tokens: list[Token] = []


    @classmethod
    def from_file(cls, path: str | Path) -> "DjuleLexer":
        """Create a lexer from a file path by reading the full source text."""
        return cls(Path(path).read_text())

    def tokenize(self) -> list[Token]:
        """Tokenize the full source into Djule tokens.

        This is the main scanning loop. It handles indentation only at real
        line starts outside parentheses, skips comments and plain whitespace,
        and dispatches to specialized lexers for strings, markup, names, and
        numbers. If a block opener such as `:` is not followed by an indented
        body before EOF, it raises a lexer error instead of inventing tokens.
        """
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
                self._expects_indent = self.paren_depth == 0 and self._last_significant_token_type == TokenType.COLON
                self._push_token(TokenType.NEWLINE, "", self.line, self.column)
                self.advance()
                continue

            if self._starts_string():
                self._lex_string()
                continue

            if self._starts_markup_declaration():
                self._lex_markup_declaration()
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

        if self._expects_indent or (self.paren_depth == 0 and self._last_significant_token_type == TokenType.COLON):
            raise LexerError("Expected indented block", self.line, 1)

        if self.tokens and self.tokens[-1].type != TokenType.NEWLINE:
            self._push_token(TokenType.NEWLINE, "", self.line, self.column)

        while len(self.indent_stack) > 1:
            self.indent_stack.pop()
            self._push_token(TokenType.DEDENT, "", self.line, self.column)

        self._push_token(TokenType.EOF, "", self.line, self.column)
        return self.tokens

    def _handle_indentation(self) -> None:
        """Translate leading spaces on the current line into indent tokens.

        Blank lines and comment-only lines are ignored and do not change the
        indentation stack. Real tabs are rejected outright. If the previous
        significant token required a block body, this method enforces that the
        next real line indents. Otherwise, extra indentation is rejected unless
        it matches a previously opened block level during dedent.
        """
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
        if self._expects_indent:
            if indent <= current_indent:
                raise LexerError("Expected indented block", self.line, 1)
            self.indent_stack.append(indent)
            self._push_token(TokenType.INDENT, "", self.line, 1)
            self._expects_indent = False
        elif indent > current_indent:
            raise LexerError("Unexpected indentation", self.line, 1)
        elif indent < current_indent:
            while len(self.indent_stack) > 1 and indent < self.indent_stack[-1]:
                self.indent_stack.pop()
                self._push_token(TokenType.DEDENT, "", self.line, 1)
            if indent != self.indent_stack[-1]:
                raise LexerError("Inconsistent indentation", self.line, 1)
        

    def _skip_comment(self) -> None:
        """Advance past a comment body until the next newline or EOF."""
        while not self.is_at_end() and self.peek() != "\n":
            self.advance()

    def _lex_identifier_or_keyword(self) -> None:
        """Lex a Python-style identifier and resolve reserved words.

        Identifiers may contain letters, digits, and underscores after the
        first character. The final text is mapped through `TokenType` so words
        like `if`, `for`, and `return` become keyword tokens instead of generic
        names.
        """
        line, column = self.line, self.column
        start = self.index
        while not self.is_at_end() and (self.peek().isalnum() or self.peek() == "_"):
            self.advance()
        value = self.source[start:self.index]
        token_type = TokenType.from_identifier(value)
        self._push_token(token_type, value, line, column)

    def _lex_number(self) -> None:
        """Lex an integer or simple decimal number literal.

        The lexer accepts a single fractional part like `1.25`. More complex
        Python numeric forms such as exponents or underscores are not handled
        here and will currently fall through to later validation.
        """
        line, column = self.line, self.column
        start = self.index
        while not self.is_at_end() and self.peek().isdigit():
            self.advance()
        if not self.is_at_end() and self.peek() == "." and self.peek(1).isdigit():
            self.advance()
            while not self.is_at_end() and self.peek().isdigit():
                self.advance()
        self._push_token(TokenType.NUMBER, self.source[start:self.index], line, column)

    def _starts_string(self) -> bool:
        """Return whether the current position begins a supported string literal.

        Djule accepts plain quoted strings and the small prefix combinations
        defined in `STRING_PREFIX_CHARS`, such as `f""` or `rf""`. It only
        checks the opening shape here; full validation happens in `_lex_string`.
        """
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
        """Lex a quoted Python-style string literal as one opaque token.

        Prefixes like `f` or `rf` are consumed as part of the token. Escaped
        characters keep the closing quote from terminating the string early.
        If EOF is reached before the matching quote, an unterminated string
        error is raised.
        """
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

        self._push_token(TokenType.STRING, self.source[start:self.index], line, column)

    def _match_punctuation(self) -> bool:
        """Emit a punctuation token when the current character is recognized.

        This includes grouping delimiters, commas, dots, colons, and bare
        braces used in Djule markup expressions. Parenthesis depth is updated
        here so indentation logic can ignore line breaks inside grouped regions.
        """
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

        self._push_token(token_type, ch, self.line, self.column)
        self.advance()

        if token_type == TokenType.LPAREN:
            self.paren_depth += 1
        elif token_type == TokenType.RPAREN and self.paren_depth > 0:
            self.paren_depth -= 1
        return True

    def _match_operator(self) -> bool:
        """Emit the longest matching operator token at the current position.

        Multi-character operators are tried before single-character ones so
        inputs like `==` or `>=` do not get split into smaller pieces.
        """
        for operator in MULTI_CHAR_OPERATORS:
            if self.source.startswith(operator, self.index):
                self._push_token(TokenType.OPERATOR, operator, self.line, self.column)
                self._advance_text(operator)
                return True

        if self.peek() in SINGLE_CHAR_OPERATORS:
            self._push_token(TokenType.OPERATOR, self.peek(), self.line, self.column)
            self.advance()
            return True

        return False

    def _starts_markup_fragment(self) -> bool:
        """Return whether the current position begins an HTML or component tag.

        A fragment starts with `<` followed by a letter, or `</` followed by a
        letter for closing tags. Other `<` sequences are left alone so they can
        fail later as ordinary invalid input instead of being misclassified.
        """
        if self.peek() != "<":
            return False

        next_char = self.peek(1)
        if next_char.isalpha():
            return True

        return next_char == "/" and self.peek(2).isalpha()

    def _starts_markup_declaration(self) -> bool:
        """Return whether the current position begins a supported declaration.

        Djule currently recognizes HTML-style declarations that start with
        `<!doctype`, case-insensitively. Other `<!...>` forms are left
        unsupported for now so they can fail explicitly instead of being
        mis-tokenized as text.
        """
        if self.peek() != "<" or self.peek(1) != "!":
            return False
        return self.source[self.index : self.index + 9].lower() == "<!doctype"

    def _lex_markup_fragment(self) -> None:
        """Lex a complete markup fragment, including nested child tags.

        The lexer keeps a simple stack of open tags so nested markup can be
        tokenized in one pass. It also allows embedded Djule `{...}` blocks
        inside markup. If a closing tag is missing or mismatched, it raises a
        lexer error at the point where the structure becomes invalid.
        """
        tag_stack: list[tuple[str, bool]] = []

        while not self.is_at_end():
            if self._starts_markup_declaration():
                self._lex_markup_declaration()
                continue

            if self._starts_markup_fragment():
                name, is_component, is_closing, is_self_closing = self._lex_tag()
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
                elif not is_self_closing:
                    tag_stack.append((name, is_component))
                continue

            if self.peek() == "{":
                self._lex_markup_expression()
                continue

            if self.peek() == "<":
                raise LexerError("Expected tag or markup declaration", self.line, self.column)

            self._lex_markup_text()

        raise LexerError("Unterminated markup fragment", self.line, self.column)

    def _lex_markup_declaration(self) -> None:
        """Lex a raw markup declaration such as `<!doctype html>`.

        The full declaration is preserved as one token because Djule currently
        emits declarations verbatim during rendering. If the closing `>` is
        missing, tokenization fails at the opening declaration boundary.
        """
        line, column = self.line, self.column
        start = self.index

        self.advance()  # <
        self.advance()  # !
        while not self.is_at_end() and self.peek() != ">":
            self.advance()

        if self.is_at_end():
            raise LexerError("Unterminated markup declaration", line, column)

        self.advance()  # >
        self._push_token(TokenType.DECLARATION, self.source[start:self.index], line, column)

    def _lex_tag(self) -> tuple[str, bool, bool, bool]:
        """Lex one opening or closing tag and return its classification.

        The returned tuple is `(name, is_component, is_closing, is_self_closing)`.
        Tag names may include `_`, `-`, and `.` so namespaced component tags are
        supported. Opening tags may end with `/>`; closing tags must still end
        with a plain `>`.
        """
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
        self._push_token(token_type, name, line, column)

        if not is_closing:
            self._lex_tag_attributes(name, line, column)

        while not self.is_at_end() and self.peek() in " \t":
            self.advance()

        if not is_closing and self.peek() == "/" and self.peek(1) == ">":
            self._push_token(TokenType.SELF_TAG_END, "/>", self.line, self.column)
            self.advance()
            self.advance()
            return name, is_component, is_closing, True

        if self.peek() != ">":
            raise LexerError("Expected > to close tag", self.line, self.column)

        self._push_token(TokenType.TAG_END, ">", self.line, self.column)
        self.advance()
        return name, is_component, is_closing, False

    def _lex_tag_attributes(self, tag_name: str, tag_line: int, tag_column: int) -> None:
        """Lex all attributes for the current opening tag.

        Attribute names accept alphanumerics plus `_`, `-`, and `:`. Values
        must be either quoted strings or Djule `{...}` expressions. If another
        tag start appears before `>`, this method reports the opening tag as
        unclosed so malformed multiline tags get a more useful error.
        """
        while not self.is_at_end():
            while not self.is_at_end() and self.peek() in " \t\r\n":
                self.advance()

            if self.peek() in {">", ""}:
                return

            if self.peek() == "<":
                raise LexerError(f"Expected > to close tag <{tag_name}>", tag_line, tag_column)
            if self.peek() == "/":
                if self.peek(1) == ">":
                    return
                raise LexerError(f"Expected > to close tag <{tag_name}>", tag_line, tag_column)

            line, column = self.line, self.column
            start = self.index
            while not self.is_at_end() and (self.peek().isalnum() or self.peek() in {"_", "-", ":"}):
                self.advance()
            name = self.source[start:self.index]
            if not name:
                raise LexerError("Expected attribute name", line, column)
            self._push_token(TokenType.ATTR_NAME, name, line, column)

            while not self.is_at_end() and self.peek() in " \t":
                self.advance()

            if self.peek() != "=":
                raise LexerError("Expected = after attribute name", self.line, self.column)
            self._push_token(TokenType.EQUALS, "=", self.line, self.column)
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
        """Lex a Djule `{...}` region embedded inside markup.

        The outer braces become normal tokens and the inner source is lexed by
        a nested `DjuleLexer`. Nested braces are tracked so expressions like
        dictionaries or nested Djule blocks do not terminate early. Quotes and
        escapes are also respected while searching for the matching `}`.
        """
        line, column = self.line, self.column
        self._push_token(TokenType.LBRACE, "{", line, column)
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
                    self._emit_embedded_source_tokens("".join(chars), line, column)
                    self._push_token(TokenType.RBRACE, "}", self.line, self.column - 1)
                    return
                chars.append(ch)
                continue

            chars.append(ch)

        raise LexerError("Unterminated markup expression", line, column)

    def _emit_embedded_source_tokens(self, source: str, line: int, column: int) -> None:
        """Re-lex embedded `{...}` source and map tokens back to outer positions.

        Multiline embedded source is normalized first so its indentation starts
        from a clean base for the inner lexer. After tokenization, each inner
        token is remapped onto the original outer line and column coordinates
        so downstream parser errors still point at the real file.
        """
        normalized_source, base_indent = self._normalize_embedded_source(source)
        inner_lexer = DjuleLexer(normalized_source)
        inner_tokens = inner_lexer.tokenize()

        if inner_tokens and inner_tokens[-1].type == TokenType.EOF:
            inner_tokens.pop()

        if normalized_source and not normalized_source.endswith("\n") and inner_tokens and inner_tokens[-1].type == TokenType.NEWLINE:
            inner_tokens.pop()

        for token in inner_tokens:
            mapped_line = line + token.line - 1
            mapped_column = column + token.column if token.line == 1 else token.column + base_indent
            self._push_token(token.type, token.value, mapped_line, mapped_column)

    @staticmethod
    def _normalize_embedded_source(source: str) -> tuple[str, int]:
        """Normalize multiline embedded source before lexing it recursively.

        The first non-empty inner indentation level is treated as the base and
        removed from later lines. Blank lines are preserved. The returned
        `base_indent` lets callers map nested token columns back to the outer
        source coordinates afterward.
        """
        if "\n" not in source:
            return source, 0

        lines = source.splitlines()
        indents = [
            len(line) - len(line.lstrip(" "))
            for line in lines[1:]
            if line.strip()
        ]
        if not indents:
            return source, 0

        base_indent = min(indents)
        normalized_lines = [lines[0]]
        for raw_line in lines[1:]:
            stripped = raw_line.lstrip(" ")
            if not stripped:
                normalized_lines.append("")
                continue
            normalized_lines.append(raw_line[base_indent:] if len(raw_line) >= base_indent else stripped)

        normalized = "\n".join(normalized_lines)
        if source.endswith("\n"):
            normalized += "\n"
        return normalized, base_indent

    def _lex_markup_text(self) -> None:
        """Lex raw text content between markup tags and Djule expressions.

        Pure whitespace-only runs are discarded so formatting indentation does
        not become text output. Non-empty text is normalized through
        `_normalize_markup_text` before a token is emitted.
        """
        line, column = self.line, self.column
        start = self.index
        while not self.is_at_end() and self.peek() not in {"<", "{"}:
            self.advance()

        raw_text = self.source[start:self.index]
        normalized = self._normalize_markup_text(raw_text)
        if normalized is not None:
            self._push_token(TokenType.TEXT, normalized, line, column)

    @staticmethod
    def _normalize_markup_text(text: str) -> str | None:
        """Normalize text nodes while dropping formatting-only whitespace.

        Single-line text is preserved as-is. Multiline text is stripped line by
        line and joined with spaces so template indentation does not leak into
        rendered output. Whitespace-only runs return `None` to signal that no
        text token should be emitted.
        """
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
        """Return whether the current cursor is at or past the source end."""
        return self.index >= self.length

    def peek(self, offset: int = 0) -> str:
        """Return the character at the current position plus `offset`.

        If the requested position is past EOF, this returns an empty string
        instead of raising, which keeps scanner boundary checks simple.
        """
        position = self.index + offset
        if position >= self.length:
            return ""
        return self.source[position]

    def advance(self) -> str:
        """Consume and return the current character, updating line state.

        Newlines advance the source line and reset the column to one. Any other
        character increments the column and marks the lexer as no longer being
        at a line start.
        """
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
        """Advance once per character in `text`, updating line/column state."""
        for _ in text:
            self.advance()

    def _push_token(self, token_type: TokenType, value: str, line: int, column: int) -> None:
        """Append a token and update significant-token bookkeeping.

        `NEWLINE`, `INDENT`, and `DEDENT` do not count as significant for block
        expectation checks. Everything else updates `_last_significant_token_type`
        so the lexer can tell whether the next real line must indent.
        """
        self.tokens.append(Token(token_type, value, line, column))
        if token_type not in {TokenType.NEWLINE, TokenType.INDENT, TokenType.DEDENT}:
            self._last_significant_token_type = token_type
