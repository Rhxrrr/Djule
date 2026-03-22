from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union


@dataclass(frozen=True)
class PythonExpr:
    source: str
    line: int = field(default=0, compare=False)
    column: int = field(default=0, compare=False)
    type: str = field(init=False, default="PythonExpr")


@dataclass(frozen=True)
class ImportFrom:
    module: str
    names: list[str]
    type: str = field(init=False, default="ImportFrom")


@dataclass(frozen=True)
class ImportModule:
    module: str
    alias: str | None = None
    type: str = field(init=False, default="ImportModule")


ImportNode = Union[ImportFrom, ImportModule]


@dataclass(frozen=True)
class AttributeNode:
    name: str
    value: str | PythonExpr
    type: str = field(init=False, default="AttributeNode")


@dataclass(frozen=True)
class TextNode:
    value: str
    type: str = field(init=False, default="TextNode")


@dataclass(frozen=True)
class ExpressionNode:
    source: str
    line: int = field(default=0, compare=False)
    column: int = field(default=0, compare=False)
    type: str = field(init=False, default="ExpressionNode")


@dataclass(frozen=True)
class EmbeddedExprNode:
    source: str
    line: int = field(default=0, compare=False)
    column: int = field(default=0, compare=False)
    type: str = field(init=False, default="EmbeddedExprNode")


@dataclass(frozen=True)
class ElementNode:
    tag: str
    attributes: list[AttributeNode]
    children: list["MarkupNode"]
    type: str = field(init=False, default="ElementNode")


@dataclass(frozen=True)
class ComponentNode:
    name: str
    attributes: list[AttributeNode]
    children: list["MarkupNode"]
    type: str = field(init=False, default="ComponentNode")


@dataclass(frozen=True)
class AssignStmt:
    target: str
    value: "AssignValue"
    type: str = field(init=False, default="AssignStmt")


@dataclass(frozen=True)
class ExprStmt:
    value: PythonExpr
    type: str = field(init=False, default="ExprStmt")


@dataclass(frozen=True)
class IfStmt:
    test: PythonExpr
    body: list["Statement"]
    orelse: list["Statement"]
    type: str = field(init=False, default="IfStmt")


@dataclass(frozen=True)
class ForStmt:
    target: str
    iter: PythonExpr
    body: list["Statement"]
    type: str = field(init=False, default="ForStmt")


Statement = Union[AssignStmt, ExprStmt, IfStmt, ForStmt]


@dataclass(frozen=True)
class ReturnStmt:
    value: "MarkupNode"
    type: str = field(init=False, default="ReturnStmt")


@dataclass(frozen=True)
class ComponentDef:
    name: str
    params: list[str]
    body: list[Statement]
    return_stmt: ReturnStmt
    type: str = field(init=False, default="ComponentDef")


@dataclass(frozen=True)
class EmbeddedAssignNode:
    target: str
    value: "AssignValue"
    type: str = field(init=False, default="EmbeddedAssignNode")


@dataclass(frozen=True)
class EmbeddedIfNode:
    test: PythonExpr
    body: list["BlockItem"]
    orelse: list["BlockItem"]
    type: str = field(init=False, default="EmbeddedIfNode")


@dataclass(frozen=True)
class EmbeddedForNode:
    target: str
    iter: PythonExpr
    body: list["BlockItem"]
    type: str = field(init=False, default="EmbeddedForNode")


@dataclass(frozen=True)
class BlockNode:
    statements: list["BlockItem"]
    type: str = field(init=False, default="BlockNode")


MarkupNode = Union[ElementNode, ComponentNode, TextNode, ExpressionNode, BlockNode]
AssignValue = Union[PythonExpr, MarkupNode]
BlockItem = Union[MarkupNode, EmbeddedAssignNode, EmbeddedIfNode, EmbeddedForNode, EmbeddedExprNode]


@dataclass(frozen=True)
class Module:
    imports: list[ImportNode]
    components: list[ComponentDef]
    type: str = field(init=False, default="Module")
