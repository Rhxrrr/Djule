from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

from djule.parser.ast_nodes import MarkupNode


@dataclass(frozen=True)
class StaticPart:
    value: str
    type: str = field(init=False, default="StaticPart")


@dataclass(frozen=True)
class ExprPart:
    source: str
    line: int = 0
    column: int = 0
    type: str = field(init=False, default="ExprPart")


@dataclass(frozen=True)
class AttrExprPart:
    source: str
    line: int = 0
    column: int = 0
    type: str = field(init=False, default="AttrExprPart")


@dataclass(frozen=True)
class NodePart:
    node: MarkupNode
    type: str = field(init=False, default="NodePart")


PlanPart = Union[StaticPart, ExprPart, AttrExprPart, NodePart]


@dataclass(frozen=True)
class ComponentPlan:
    name: str
    parts: list[PlanPart]
    requires_runtime_body: bool = False
    type: str = field(init=False, default="ComponentPlan")
