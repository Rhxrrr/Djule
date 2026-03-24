from __future__ import annotations

from dataclasses import dataclass
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
    NAME = "NAME"
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
    OPERATOR = "OPERATOR"
    NEWLINE = "NEWLINE"
    INDENT = "INDENT"
    DEDENT = "DEDENT"

    # Markup tokens
    HTML_TAG_OPEN = "HTML_TAG_OPEN"
    HTML_TAG_CLOSE = "HTML_TAG_CLOSE"
    COMPONENT_TAG_OPEN = "COMPONENT_TAG_OPEN"
    COMPONENT_TAG_CLOSE = "COMPONENT_TAG_CLOSE"
    TAG_END = "TAG_END"
    ATTR_NAME = "ATTR_NAME"
    TEXT = "TEXT"
    EXPR = "EXPR"

    EOF = "EOF"


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
