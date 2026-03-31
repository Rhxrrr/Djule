from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

@dataclass(frozen=True)
class PythonExpr:
    """A Python expression preserved as source text plus source coordinates."""
    source: str
    line: int = field(default=0, compare=False)
    column: int = field(default=0, compare=False)
    type: str = field(init=False, default="PythonExpr")


@dataclass(frozen=True)
class ImportFrom:
    """A `from module import name1, name2` import node."""
    module: str
    names: list[str]
    line: int = field(default=0, compare=False)
    column: int = field(default=0, compare=False)
    type: str = field(init=False, default="ImportFrom")


@dataclass(frozen=True)
class ImportModule:
    """An `import module [as alias]` import node."""
    module: str
    alias: str | None = None
    line: int = field(default=0, compare=False)
    column: int = field(default=0, compare=False)
    type: str = field(init=False, default="ImportModule")


ImportNode = Union[ImportFrom, ImportModule]


@dataclass(frozen=True)
class AttributeNode:
    """One markup attribute whose value is either literal text or a Python expression."""
    name: str
    value: str | PythonExpr
    type: str = field(init=False, default="AttributeNode")


@dataclass(frozen=True)
class TextNode:
    """Raw text content that should be emitted directly in markup."""
    value: str
    type: str = field(init=False, default="TextNode")


@dataclass(frozen=True)
class ExpressionNode:
    """A `{...}` markup interpolation that renders one Python expression value."""
    source: str
    line: int = field(default=0, compare=False)
    column: int = field(default=0, compare=False)
    type: str = field(init=False, default="ExpressionNode")


@dataclass(frozen=True)
class EmbeddedExprNode:
    """A bare expression line inside an embedded Djule block."""
    source: str
    line: int = field(default=0, compare=False)
    column: int = field(default=0, compare=False)
    type: str = field(init=False, default="EmbeddedExprNode")


@dataclass(frozen=True)
class DeclarationNode:
    """A raw markup declaration such as `<!doctype html>`."""
    value: str
    type: str = field(init=False, default="DeclarationNode")

@dataclass(frozen=True)
class FragmentNode:
    """A transparent container for adjacent markup nodes returned together."""
    children: list["MarkupNode"]
    type: str = field(init=False, default="FragmentNode")


@dataclass(frozen=True)
class ElementNode:
    """A plain HTML-like tag with attributes and child markup nodes."""
    tag: str
    attributes: list[AttributeNode]
    children: list["MarkupNode"]
    self_closing: bool = False
    type: str = field(init=False, default="ElementNode")


@dataclass(frozen=True)
class ComponentNode:
    """A component tag reference with props and nested child markup."""
    name: str
    attributes: list[AttributeNode]
    children: list["MarkupNode"]
    self_closing: bool = False
    line: int = field(default=0, compare=False)
    column: int = field(default=0, compare=False)
    type: str = field(init=False, default="ComponentNode")


@dataclass(frozen=True)
class AssignStmt:
    """A top-level component-body assignment statement."""
    target: str
    value: "AssignValue"
    type: str = field(init=False, default="AssignStmt")


@dataclass(frozen=True)
class ExprStmt:
    """A standalone Python expression statement in component code."""
    value: PythonExpr
    type: str = field(init=False, default="ExprStmt")


@dataclass(frozen=True)
class IfStmt:
    """A top-level `if` / `else` statement in component code."""
    test: PythonExpr
    body: list["Statement"]
    orelse: list["Statement"]
    type: str = field(init=False, default="IfStmt")


@dataclass(frozen=True)
class ForStmt:
    """A top-level `for ... in ...` loop in component code."""
    target: str
    iter: PythonExpr
    body: list["Statement"]
    type: str = field(init=False, default="ForStmt")


Statement = Union[AssignStmt, ExprStmt, IfStmt, ForStmt]


@dataclass(frozen=True)
class ReturnStmt:
    """The required `return (...)` markup statement of a component."""
    value: "MarkupNode"
    type: str = field(init=False, default="ReturnStmt")


@dataclass(frozen=True)
class ComponentDef:
    """A Djule component definition with params, body statements, and returned markup."""
    name: str
    params: list[str]
    body: list[Statement]
    return_stmt: ReturnStmt
    defaults: dict[str, PythonExpr] = field(default_factory=dict)
    type: str = field(init=False, default="ComponentDef")


@dataclass(frozen=True)
class EmbeddedAssignNode:
    """An assignment statement inside an embedded `{...}` block."""
    target: str
    value: "AssignValue"
    type: str = field(init=False, default="EmbeddedAssignNode")


@dataclass(frozen=True)
class EmbeddedIfNode:
    """An `if` / `else` block nested inside markup braces."""
    test: PythonExpr
    body: list["BlockItem"]
    orelse: list["BlockItem"]
    type: str = field(init=False, default="EmbeddedIfNode")


@dataclass(frozen=True)
class EmbeddedForNode:
    """A `for ... in ...` block nested inside markup braces."""
    target: str
    iter: PythonExpr
    body: list["BlockItem"]
    type: str = field(init=False, default="EmbeddedForNode")


@dataclass(frozen=True)
class BlockNode:
    """A container for embedded Djule block items inside markup."""
    statements: list["BlockItem"]
    type: str = field(init=False, default="BlockNode")


MarkupNode = Union[FragmentNode, DeclarationNode, ElementNode, ComponentNode, TextNode, ExpressionNode, BlockNode]
AssignValue = Union[PythonExpr, MarkupNode]
BlockItem = Union[MarkupNode, EmbeddedAssignNode, EmbeddedIfNode, EmbeddedForNode, EmbeddedExprNode]


@dataclass(frozen=True)
class Module:
    """The root AST node for one Djule source file."""
    imports: list[ImportNode]
    components: list[ComponentDef]
    type: str = field(init=False, default="Module")
