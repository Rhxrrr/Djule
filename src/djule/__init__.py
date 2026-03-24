"""Public package surface for Djule."""

from importlib.metadata import PackageNotFoundError, version

from .compiler import DjuleRenderer, RendererError, SafeHtml

try:
    __version__ = version("djule")
except PackageNotFoundError:  # pragma: no cover - local source tree before install
    __version__ = "0.0.0"

__all__ = ["DjuleRenderer", "RendererError", "SafeHtml", "__version__"]
