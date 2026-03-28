from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Union

from djule.parser.ast_nodes import ComponentDef


class SafeHtml(str):
    """A rendered HTML fragment that should not be escaped again."""


ExternalComponent = Union[Callable[..., object], ComponentDef]


@dataclass(frozen=True)
class ImportedComponentRef:
    """A resolved component reference that points into another renderer/module."""
    renderer: "DjuleRenderer"
    component_name: str


@dataclass
class RendererError(Exception):
    """A runtime or compilation error raised while rendering Djule output."""
    message: str

    def __str__(self) -> str:
        """Return the stored renderer error message unchanged."""
        return self.message
