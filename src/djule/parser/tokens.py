from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from enum import Enum


class TokenType(str, Enum):
    # Python/top-level tokens
    FROM = "FROM"
    IMPORT = "IMPORT"
    AS = "AS"
    DEF = "DEF"
    RETURN = "RETURN"
    IF = "IF"
    ELSE = "ELSE"
    FOR = "FOR"
    IN = "IN"
    NOT = "NOT"
    NAME = "NAME"  # component, function, param, module, import, and local variable names
    STRING = "STRING"
    NUMBER = "NUMBER"
    LPAREN = "LPAREN"
    RPAREN = "RPAREN"
    LBRACKET = "LBRACKET"
    RBRACKET = "RBRACKET"
    LBRACE = "LBRACE"
    RBRACE = "RBRACE"
    COLON = "COLON"
    COMMA = "COMMA"
    DOT = "DOT"
    EQUALS = "EQUALS"
    OPERATOR = "OPERATOR"  # +=, ==, !=, >=, <=, +, -, *, /, >, <
    NEWLINE = "NEWLINE"
    INDENT = "INDENT"
    DEDENT = "DEDENT"

    # Markup tokens
    HTML_TAG_OPEN = "HTML_TAG_OPEN"  # opening HTML tag name token (e.g. "div" from <div>)
    HTML_TAG_CLOSE = "HTML_TAG_CLOSE"  # closing HTML tag name token (e.g. "div" from </div>)
    COMPONENT_TAG_OPEN = "COMPONENT_TAG_OPEN"  # opening component tag name token (e.g. "Button")
    COMPONENT_TAG_CLOSE = "COMPONENT_TAG_CLOSE"  # closing component tag name token (e.g. "Button")
    TAG_END = "TAG_END"  # literal ">" that ends an opening/closing tag
    ATTR_NAME = "ATTR_NAME"  # attribute name inside a tag (e.g. class, id, custom props)
    TEXT = "TEXT"  # plain text content inside markup
    EXPR = "EXPR"  # embedded expression inside markup: { python_expression }

    EOF = "EOF"  # end of file

    @classmethod
    @cache # avoids rebuilding the dict on every call
    def _identifier_map(cls) -> dict[str, "TokenType"]:
        return {
            "from": cls.FROM,
            "import": cls.IMPORT,
            "as": cls.AS,
            "def": cls.DEF,
            "return": cls.RETURN,
            "if": cls.IF,
            "else": cls.ELSE,
            "for": cls.FOR,
            "in": cls.IN,
            "not": cls.NOT,
        }

    @classmethod
    def from_identifier(cls, value: str) -> "TokenType":
        return cls._identifier_map().get(value, cls.NAME)

MULTI_CHAR_OPERATORS = ("+=", "==", "!=", ">=", "<=")
SINGLE_CHAR_OPERATORS = {"+", "-", "*", "/", ">", "<"}
STRING_PREFIX_CHARS = set("fFrRuUbB")


@dataclass(frozen=True)
class Token:
    type: TokenType
    value: str
    line: int
    column: int

    def __str__(self) -> str:
        location = f"{self.line}:{self.column}"
        if self.value:
            return f"{location} {self.type.value} {self.value!r}"
        return f"{location} {self.type.value}"
