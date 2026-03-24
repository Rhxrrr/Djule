"""Framework integrations for Djule."""

from .django import get_djule_search_paths, render_djule, render_djule_response, resolve_djule_template

__all__ = [
    "get_djule_search_paths",
    "render_djule",
    "render_djule_response",
    "resolve_djule_template",
]
