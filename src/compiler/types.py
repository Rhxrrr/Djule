from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Union

from src.parser.ast_nodes import ComponentDef


class SafeHtml(str):
    """A rendered HTML fragment that should not be escaped again."""


ExternalComponent = Union[Callable[..., object], ComponentDef]


@dataclass(frozen=True)
class ImportedComponentRef:
    renderer: "DjuleRenderer"
    component_name: str


@dataclass
class RendererError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message
