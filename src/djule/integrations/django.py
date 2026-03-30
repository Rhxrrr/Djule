from __future__ import annotations

import inspect
import os
import re
import sys
from functools import wraps
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


def _django_template_options(settings_obj=None) -> list[dict[str, object]]:
    """Return Django template OPTIONS blocks from configured template backends."""
    settings = _get_settings(settings_obj)
    options_list: list[dict[str, object]] = []

    for template in getattr(settings, "TEMPLATES", []) or []:
        if not isinstance(template, dict):
            continue
        if template.get("BACKEND") != "django.template.backends.django.DjangoTemplates":
            continue
        options = template.get("OPTIONS")
        if isinstance(options, dict):
            options_list.append(options)

    return options_list


def _find_manage_py(start_path: Path | None) -> Path | None:
    """Walk upward from `start_path` until a Django `manage.py` is found."""
    if start_path is None:
        return None

    current = start_path.resolve()
    if current.is_file():
        current = current.parent

    while True:
        candidate = current / "manage.py"
        if candidate.exists():
            return candidate
        if current.parent == current:
            return None
        current = current.parent


def _settings_module_from_manage_py(manage_py_path: Path) -> str | None:
    """Extract `DJANGO_SETTINGS_MODULE` from a standard Django `manage.py` file."""
    try:
        source = manage_py_path.read_text(encoding="utf-8")
    except OSError:
        return None

    match = re.search(r"DJANGO_SETTINGS_MODULE[\"']?\s*,\s*[\"']([^\"']+)[\"']", source)
    if match:
        return match.group(1)
    return None


def _ensure_django_settings(
    *,
    settings_obj=None,
    settings_module: str | None = None,
    document_path: str | Path | None = None,
    workspace_path: str | Path | None = None,
):
    """Return configured Django settings, auto-bootstrapping when possible."""
    if settings_obj is not None:
        return settings_obj

    try:
        from django.conf import settings
        import django
    except ModuleNotFoundError:
        return None

    if settings.configured:
        return settings

    candidate_paths: list[Path] = []
    if document_path:
        candidate_paths.append(Path(document_path))
    if workspace_path:
        candidate_paths.append(Path(workspace_path))
    candidate_paths.append(Path.cwd())

    resolved_settings_module = settings_module or os.environ.get("DJANGO_SETTINGS_MODULE")
    project_root: Path | None = None

    if not resolved_settings_module:
        for candidate in candidate_paths:
            manage_py_path = _find_manage_py(candidate)
            if manage_py_path is None:
                continue
            project_root = manage_py_path.parent
            resolved_settings_module = _settings_module_from_manage_py(manage_py_path)
            if resolved_settings_module:
                break

    if project_root is not None:
        project_root_str = str(project_root.resolve())
        if project_root_str not in sys.path:
            sys.path.insert(0, project_root_str)

    if not resolved_settings_module:
        return None

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", resolved_settings_module)

    try:
        django.setup()
    except Exception:
        return None

    return settings


def _iter_django_builtin_libraries(
    *,
    settings_obj=None,
    settings_module: str | None = None,
    document_path: str | Path | None = None,
    workspace_path: str | Path | None = None,
):
    """Yield Django template builtin libraries that should be globally available."""
    settings = _ensure_django_settings(
        settings_obj=settings_obj,
        settings_module=settings_module,
        document_path=document_path,
        workspace_path=workspace_path,
    )

    if settings is not None and settings_obj is None:
        try:
            from django.template import engines
        except ModuleNotFoundError:
            return

        seen_libraries: set[int] = set()
        for backend in engines.all():
            engine = getattr(backend, "engine", None)
            template_builtins = getattr(engine, "template_builtins", None)
            if not template_builtins:
                continue
            for library in template_builtins:
                library_id = id(library)
                if library_id in seen_libraries:
                    continue
                seen_libraries.add(library_id)
                yield library
        return

    seen_modules: set[str] = set()
    for options in _django_template_options(settings_obj):
        for module_name in options.get("builtins", []) or []:
            if not isinstance(module_name, str) or module_name in seen_modules:
                continue
            seen_modules.add(module_name)
            try:
                module = import_module(module_name)
            except Exception:
                continue
            library = getattr(module, "register", None)
            if library is not None:
                yield library


def _tag_runtime_wrapper(
    name: str,
    compile_func,
    *,
    request=None,
    base_context: Mapping[str, object] | None = None,
):
    """Adapt a Django `simple_tag`-style callable into a Djule builtin."""
    original = inspect.unwrap(compile_func)
    if not callable(original):
        return None

    nonlocals = inspect.getclosurevars(compile_func).nonlocals
    takes_context = bool(nonlocals.get("takes_context", False))
    if not takes_context:
        return original

    captured_context = dict(base_context or {})
    if request is not None and "request" not in captured_context:
        captured_context["request"] = request

    @wraps(original)
    def _wrapped(*args, **kwargs):
        return original(captured_context, *args, **kwargs)

    _wrapped.__name__ = name
    return _wrapped


def get_djule_template_tag_builtins(
    *,
    request=None,
    base_context: Mapping[str, object] | None = None,
    settings_obj=None,
    settings_module: str | None = None,
    document_path: str | Path | None = None,
    workspace_path: str | Path | None = None,
) -> dict[str, object]:
    """Return Django builtin template tags adapted for Djule expression calls."""
    builtins: dict[str, object] = {}

    for library in _iter_django_builtin_libraries(
        settings_obj=settings_obj,
        settings_module=settings_module,
        document_path=document_path,
        workspace_path=workspace_path,
    ):
        tags = getattr(library, "tags", {})
        if not isinstance(tags, Mapping):
            continue
        for name, compile_func in tags.items():
            if not isinstance(name, str) or not callable(compile_func) or not hasattr(compile_func, "__wrapped__"):
                continue
            wrapped = _tag_runtime_wrapper(
                name,
                compile_func,
                request=request,
                base_context=base_context,
            )
            if wrapped is not None:
                builtins[name] = wrapped

    return builtins


def _editor_request_stub() -> SimpleNamespace:
    """Build a forgiving request-like object for editor-side context discovery."""
    return SimpleNamespace(
        COOKIES={},
        FILES={},
        GET={},
        META={},
        POST={},
        headers={},
        method="GET",
        path="/",
        path_info="/",
        session={},
        user=SimpleNamespace(
            email="",
            first_name="",
            id=None,
            is_authenticated=False,
            is_staff=False,
            is_superuser=False,
            last_name="",
            username="",
        ),
    )


def _schema_detail_for_value(value: object) -> str:
    """Return a compact human-readable description for one discovered value."""
    if callable(value):
        try:
            signature = str(inspect.signature(value))
        except (TypeError, ValueError):
            signature = "()"
        name = getattr(value, "__name__", value.__class__.__name__)
        return f"{name}{signature}"
    return value.__class__.__name__


def _schema_from_value(value: object, *, depth: int = 2, detail: str | None = None) -> dict[str, object]:
    """Convert one Python value into the nested schema used by editor globals."""
    node: dict[str, object] = {"detail": detail or _schema_detail_for_value(value)}

    if depth <= 0:
        return node

    members: dict[str, object] = {}
    if isinstance(value, Mapping):
        for key, inner in value.items():
            if isinstance(key, str) and key.isidentifier():
                members[key] = _schema_from_value(inner, depth=depth - 1)
    elif isinstance(value, SimpleNamespace):
        for key, inner in vars(value).items():
            if key.isidentifier() and not key.startswith("_"):
                members[key] = _schema_from_value(inner, depth=depth - 1)
    elif hasattr(value, "__dict__") and not callable(value):
        for key, inner in vars(value).items():
            if key.isidentifier() and not key.startswith("_"):
                members[key] = _schema_from_value(inner, depth=depth - 1)

    if members:
        node["members"] = members
    return node


def _merge_editor_schema(target: dict[str, object], incoming: Mapping[str, object]) -> dict[str, object]:
    """Merge one discovered schema map into another."""
    for name, value in incoming.items():
        if name not in target or not isinstance(target[name], dict):
            target[name] = value
            continue

        current = target[name]
        if not current.get("detail") and isinstance(value, dict) and value.get("detail"):
            current["detail"] = value["detail"]

        current_members = current.get("members")
        incoming_members = value.get("members") if isinstance(value, dict) else None
        if isinstance(current_members, dict) and isinstance(incoming_members, dict):
            _merge_editor_schema(current_members, incoming_members)
        elif incoming_members:
            current["members"] = incoming_members

    return target


def discover_djule_editor_globals(
    *,
    settings_obj=None,
    settings_module: str | None = None,
    document_path: str | Path | None = None,
    workspace_path: str | Path | None = None,
) -> dict[str, object]:
    """Discover Django-backed globals and simple tags for Djule editor tooling."""
    schema: dict[str, object] = {}
    request = _editor_request_stub()
    resolved_settings = _ensure_django_settings(
        settings_obj=settings_obj,
        settings_module=settings_module,
        document_path=document_path,
        workspace_path=workspace_path,
    )

    try:
        processors = get_djule_context_processors(settings_obj=resolved_settings or settings_obj)
    except Exception:
        processors = []

    context_values: dict[str, object] = {}
    for processor in processors:
        try:
            values = processor(request)
        except Exception:
            continue
        if isinstance(values, Mapping):
            context_values.update(values)

    for name, value in context_values.items():
        if isinstance(name, str) and name.isidentifier():
            schema[name] = _schema_from_value(value)

    tag_builtins = get_djule_template_tag_builtins(
        request=request,
        base_context=context_values,
        settings_obj=resolved_settings or settings_obj,
        settings_module=settings_module,
        document_path=document_path,
        workspace_path=workspace_path,
    )
    tag_schema = {
        name: {
            "detail": f"Django template tag {_schema_detail_for_value(value)}",
        }
        for name, value in tag_builtins.items()
        if isinstance(name, str) and name.isidentifier()
    }

    return _merge_editor_schema(schema, tag_schema)


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

    resolved_builtins = get_djule_template_tag_builtins(
        request=request,
        base_context=render_props,
        settings_obj=settings_obj,
        document_path=template_path,
        workspace_path=template_path.parent,
    )
    if builtins:
        resolved_builtins.update(builtins)

    renderer = DjuleRenderer.from_file(
        template_path,
        component_registry=component_registry,
        builtins=resolved_builtins,
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
