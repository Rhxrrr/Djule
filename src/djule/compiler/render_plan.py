from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

from djule.parser.ast_nodes import MarkupNode


@dataclass(frozen=True)
class StaticPart:
    """A pre-rendered static HTML chunk in a component render plan."""
    value: str
    type: str = field(init=False, default="StaticPart")


@dataclass(frozen=True)
class ExprPart:
    """A dynamic text expression to evaluate during render-plan execution."""
    source: str
    line: int = 0
    column: int = 0
    type: str = field(init=False, default="ExprPart")


@dataclass(frozen=True)
class AttrExprPart:
    """A dynamic attribute expression whose value must be HTML-escaped."""
    source: str
    line: int = 0
    column: int = 0
    type: str = field(init=False, default="AttrExprPart")


@dataclass(frozen=True)
class NodePart:
    """A fallback plan part that renders a full markup node at runtime."""
    node: MarkupNode
    type: str = field(init=False, default="NodePart")


PlanPart = Union[StaticPart, ExprPart, AttrExprPart, NodePart]


@dataclass(frozen=True)
class ComponentPlan:
    """A compiled render plan for one component entrypoint."""
    name: str
    parts: list[PlanPart]
    requires_runtime_body: bool = False
    type: str = field(init=False, default="ComponentPlan")
