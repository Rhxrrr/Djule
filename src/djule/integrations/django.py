from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Mapping, Sequence

from djule.compiler import DjuleRenderer

_AUTORELOAD_CONNECTED = False


def _get_settings(settings_obj=None):
    """Return the provided settings object or import Django's global settings."""
    if settings_obj is not None:
        return settings_obj

    try:
        from django.conf import settings
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional dependency
        raise RuntimeError("Django integration requires Django to be installed") from exc

    return settings


def get_djule_search_paths(
    *,
    settings_obj=None,
    extra_paths: Sequence[str | Path] | None = None,
) -> list[Path]:
    """Resolve the effective Djule template/import search roots for Django use.

    Explicit `DJULE_IMPORT_ROOTS` wins. Otherwise the integration falls back to
    `BASE_DIR` or the renderer's default search paths. Duplicate paths are
    removed while preserving order.
    """
    settings = _get_settings(settings_obj)
    configured = getattr(settings, "DJULE_IMPORT_ROOTS", None)

    paths: list[Path] = []
    seen: set[Path] = set()

    if configured:
        candidates = [Path(entry) for entry in configured]
    else:
        base_dir = getattr(settings, "BASE_DIR", None)
        candidates = [Path(base_dir)] if base_dir else DjuleRenderer._default_search_paths()

    if extra_paths:
        candidates.extend(Path(entry) for entry in extra_paths)

    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        paths.append(resolved)
        seen.add(resolved)

    return paths


def get_djule_watch_directories(
    *,
    settings_obj=None,
    extra_paths: Sequence[str | Path] | None = None,
) -> list[Path]:
    """Return existing directories that should be watched for Djule file changes."""
    directories: list[Path] = []
    seen: set[Path] = set()

    for path in get_djule_search_paths(settings_obj=settings_obj, extra_paths=extra_paths):
        if not path.exists() or not path.is_dir():
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        directories.append(resolved)
        seen.add(resolved)

    return directories


def watch_djule_files(
    reloader,
    *,
    settings_obj=None,
    extra_paths: Sequence[str | Path] | None = None,
) -> list[Path]:
    """Register `**/*.djule` watch globs with Django's autoreloader."""
    watched: list[Path] = []

    for directory in get_djule_watch_directories(settings_obj=settings_obj, extra_paths=extra_paths):
        reloader.watch_dir(directory, "**/*.djule")
        watched.append(directory)

    return watched


def trigger_browser_reload() -> bool:
    """Trigger django-browser-reload if it is installed, returning success status."""
    try:
        from django_browser_reload.views import trigger_reload_soon
    except ModuleNotFoundError:
        return False

    trigger_reload_soon()
    return True


def handle_djule_file_change(
    file_path: str | Path,
    *,
    settings_obj=None,
    extra_paths: Sequence[str | Path] | None = None,
) -> bool:
    """Handle a changed Djule file without forcing a full Django process restart.

    When a watched `.djule` file changes, Djule caches are cleared and an
    optional browser reload is triggered. Non-Djule files or paths outside the
    watched directories are ignored and return `False`.
    """
    path = Path(file_path)
    if path.suffix != ".djule":
        return False

    try:
        resolved = path.resolve()
    except FileNotFoundError:
        return False

    for directory in get_djule_watch_directories(settings_obj=settings_obj, extra_paths=extra_paths):
        if directory == resolved.parent or directory in resolved.parents:
            DjuleRenderer.clear_caches()
            trigger_browser_reload()
            return True

    return False


def ensure_djule_autoreload(
    *,
    settings_obj=None,
    extra_paths: Sequence[str | Path] | None = None,
) -> bool:
    """Register Djule autoreload hooks when Django debug auto-reload is enabled."""
    try:
        settings = _get_settings(settings_obj)
        debug_enabled = bool(getattr(settings, "DEBUG", False))
        auto_reload_enabled = bool(getattr(settings, "DJULE_AUTO_RELOAD", True))
    except Exception:
        return False

    if not debug_enabled or not auto_reload_enabled:
        return False

    register_djule_autoreload(settings_obj=settings_obj, extra_paths=extra_paths)
    return True


def register_djule_autoreload(
    *,
    settings_obj=None,
    extra_paths: Sequence[str | Path] | None = None,
):
    """Connect Djule file watching and file-changed handlers to Django autoreload once."""
    global _AUTORELOAD_CONNECTED

    if _AUTORELOAD_CONNECTED:
        return None

    try:
        from django.utils import autoreload
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional dependency
        raise RuntimeError("Django integration requires Django to be installed") from exc

    def _watcher(sender, **kwargs):
        """Register watched Djule directories when Django starts autoreload."""
        watch_djule_files(sender, settings_obj=settings_obj, extra_paths=extra_paths)

    def _file_changed(sender, file_path, **kwargs):
        """Handle one changed file reported by Django autoreload."""
        return handle_djule_file_change(
            file_path,
            settings_obj=settings_obj,
            extra_paths=extra_paths,
        )

    autoreload.autoreload_started.connect(
        _watcher,
        dispatch_uid="djule.integrations.django.autoreload",
        weak=False,
    )
    autoreload.file_changed.connect(
        _file_changed,
        dispatch_uid="djule.integrations.django.file_changed",
        weak=False,
    )
    _AUTORELOAD_CONNECTED = True
    return _watcher, _file_changed


def resolve_djule_template(template_name: str | Path, *, search_paths: Sequence[Path]) -> Path:
    """Resolve a Djule template name against the configured search paths."""
    candidate = Path(template_name)
    if candidate.is_absolute() and candidate.exists():
        return candidate.resolve()

    for base_path in search_paths:
        resolved = (base_path / candidate).resolve()
        if resolved.exists():
            return resolved

    searched = ", ".join(str((base / candidate).resolve()) for base in search_paths) or "<no search paths configured>"
    raise FileNotFoundError(f"Could not resolve Djule template '{template_name}'. Searched: {searched}")


def render_djule(
    request,
    template_name: str | Path,
    props: Mapping[str, object] | None = None,
    *,
    component_name: str | None = None,
    search_paths: Sequence[str | Path] | None = None,
    component_registry: Mapping[str, object] | None = None,
    builtins: Mapping[str, object] | None = None,
    include_request_prop: bool = False,
    settings_obj=None,
) -> str:
    """Render a Djule template to HTML for use inside a Django project.

    The helper resolves search paths from Django settings, optionally injects
    the request object into props, and ensures Djule's autoreload hook is ready
    in debug mode before rendering.
    """
    ensure_djule_autoreload(settings_obj=settings_obj, extra_paths=search_paths)
    resolved_search_paths = get_djule_search_paths(settings_obj=settings_obj, extra_paths=search_paths)
    template_path = resolve_djule_template(template_name, search_paths=resolved_search_paths)

    render_props = dict(props or {})
    if include_request_prop and request is not None and "request" not in render_props:
        render_props["request"] = request
    if request is not None and "csrf_token" not in render_props and "csrf_token_html" not in render_props:
        try:
            from django.middleware.csrf import get_token
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional dependency
            raise RuntimeError("Django integration requires Django to be installed") from exc
        render_props["csrf_token"] = get_token(request)

    renderer = DjuleRenderer.from_file(
        template_path,
        component_registry=component_registry,
        builtins=builtins,
        search_paths=resolved_search_paths,
    )
    return renderer.render(component_name=component_name, props=render_props)


def render_djule_response(
    request,
    template_name: str | Path,
    props: Mapping[str, object] | None = None,
    *,
    component_name: str | None = None,
    search_paths: Sequence[str | Path] | None = None,
    component_registry: Mapping[str, object] | None = None,
    builtins: Mapping[str, object] | None = None,
    include_request_prop: bool = False,
    status: int = 200,
    content_type: str = "text/html; charset=utf-8",
    headers: Mapping[str, str] | None = None,
    settings_obj=None,
):
    """Render a Djule template and wrap it in a Django `HttpResponse`."""
    try:
        from django.http import HttpResponse
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional dependency
        raise RuntimeError("Django integration requires Django to be installed") from exc

    html = render_djule(
        request,
        template_name,
        props,
        component_name=component_name,
        search_paths=search_paths,
        component_registry=component_registry,
        builtins=builtins,
        include_request_prop=include_request_prop,
        settings_obj=settings_obj,
    )
    return HttpResponse(html, status=status, content_type=content_type, headers=headers)


def build_request_props(**kwargs) -> SimpleNamespace:
    """Small helper for tests/examples that want attribute-style request data."""
    return SimpleNamespace(**kwargs)
