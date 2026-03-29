from __future__ import annotations

from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Mapping, Sequence

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


def _resolve_context_processor(processor: str | Callable[[object], object]) -> Callable[[object], object]:
    """Resolve one Djule context processor from a callable or dotted import path.

    String values must be fully qualified import paths. Non-callable resolved
    objects raise a targeted error so misconfigured global props fail early.
    """
    if callable(processor):
        return processor

    if not isinstance(processor, str):
        raise TypeError("Djule context processors must be callables or dotted import paths")

    module_name, separator, attr_name = processor.rpartition(".")
    if not separator or not module_name or not attr_name:
        raise ValueError(f"Invalid Djule context processor path '{processor}'")

    module = import_module(module_name)
    resolved = getattr(module, attr_name)
    if not callable(resolved):
        raise TypeError(f"Djule context processor '{processor}' did not resolve to a callable")
    return resolved


def get_djule_context_processors(
    *,
    settings_obj=None,
    extra_processors: Sequence[str | Callable[[object], object]] | None = None,
) -> list[Callable[[object], object]]:
    """Resolve the ordered Djule context processors for Django rendering.

    Djule mirrors Django's context-processor style by reading
    `TEMPLATES[..].OPTIONS.context_processors`, then appending any optional
    `DJULE_CONTEXT_PROCESSORS`. Extra processors passed directly to the render
    helper are added last. Duplicate entries are removed while preserving order.
    """
    settings = _get_settings(settings_obj)
    configured: list[str | Callable[[object], object]] = []

    for template in getattr(settings, "TEMPLATES", []) or []:
        if not isinstance(template, dict):
            continue
        if template.get("BACKEND") != "django.template.backends.django.DjangoTemplates":
            continue
        options = template.get("OPTIONS")
        if not isinstance(options, dict):
            continue
        processors = options.get("context_processors")
        if isinstance(processors, Sequence) and not isinstance(processors, (str, bytes)):
            configured.extend(processors)

    djule_specific = getattr(settings, "DJULE_CONTEXT_PROCESSORS", None)
    if isinstance(djule_specific, Sequence) and not isinstance(djule_specific, (str, bytes)):
        configured.extend(djule_specific)

    if extra_processors:
        configured.extend(extra_processors)

    resolved_processors: list[Callable[[object], object]] = []
    seen: set[object] = set()
    for processor in configured:
        if processor in seen:
            continue
        seen.add(processor)
        resolved_processors.append(_resolve_context_processor(processor))

    return resolved_processors


def build_djule_context(
    request,
    *,
    settings_obj=None,
    context_processors: Sequence[str | Callable[[object], object]] | None = None,
) -> dict[str, object]:
    """Evaluate Djule context processors into one shared props dictionary.

    Later processors override earlier ones, matching Django's general context
    layering style. Each processor may return `None` to contribute nothing; any
    other non-mapping return value raises an error so bad global context stays
    visible during development.
    """
    context: dict[str, object] = {}
    for processor in get_djule_context_processors(
        settings_obj=settings_obj,
        extra_processors=context_processors,
    ):
        values = processor(request)
        if values is None:
            continue
        if not isinstance(values, Mapping):
            processor_name = getattr(processor, "__name__", repr(processor))
            raise TypeError(f"Djule context processor '{processor_name}' must return a mapping or None")
        context.update(values)
    return context


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
    context_processors: Sequence[str | Callable[[object], object]] | None = None,
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

    render_props = build_djule_context(
        request,
        settings_obj=settings_obj,
        context_processors=context_processors,
    )
    render_props.update(props or {})
    if include_request_prop and request is not None and "request" not in render_props:
        render_props["request"] = request
    if (
        request is not None
        and hasattr(request, "META")
        and "csrf_token" not in render_props
        and "csrf_token_html" not in render_props
    ):
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
    context_processors: Sequence[str | Callable[[object], object]] | None = None,
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
        context_processors=context_processors,
        settings_obj=settings_obj,
    )
    return HttpResponse(html, status=status, content_type=content_type, headers=headers)


def build_request_props(**kwargs) -> SimpleNamespace:
    """Small helper for tests/examples that want attribute-style request data."""
    return SimpleNamespace(**kwargs)
