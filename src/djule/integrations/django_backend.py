from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Mapping

from djule.compiler import DjuleRenderer
from djule.integrations.django import (
    DJULE_TEMPLATE_BACKEND,
    build_djule_context,
    ensure_djule_autoreload,
    get_djule_template_tag_builtins,
)

try:  # pragma: no cover - exercised via integration tests when Django is installed
    from django.conf import settings as django_settings
    from django.template import Origin
    from django.template.backends.base import BaseEngine
    from django.template.engine import Engine
    from django.template.exceptions import TemplateDoesNotExist
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    django_settings = None
    Origin = None
    BaseEngine = object
    Engine = None
    TemplateDoesNotExist = RuntimeError


def _normalize_render_context(context) -> dict[str, object]:
    if context is None:
        return {}
    if isinstance(context, Mapping):
        return dict(context)

    flatten = getattr(context, "flatten", None)
    if callable(flatten):
        flattened = flatten()
        if isinstance(flattened, Mapping):
            return dict(flattened)

    return dict(context)


class DjuleTemplate:
    def __init__(
        self,
        backend: "DjuleTemplates",
        *,
        template_name: str,
        origin,
        template_path: Path | None = None,
        source: str | None = None,
    ) -> None:
        self.backend = backend
        self.template_name = template_name
        self.origin = origin
        self.template_path = template_path.resolve() if template_path is not None else None
        self.source = source

    def render(self, context=None, request=None):
        ambient_props = build_djule_context(
            request,
            settings_obj=self.backend.settings_obj,
        )
        render_props = dict(ambient_props)
        render_props.update(_normalize_render_context(context))
        if self.backend.include_request_prop and request is not None and "request" not in render_props:
            render_props["request"] = request
            ambient_props["request"] = request

        resolved_importables = get_djule_template_tag_builtins(
            request=request,
            base_context=render_props,
            settings_obj=self.backend.settings_obj,
            document_path=self.template_path,
            workspace_path=self.template_path.parent if self.template_path is not None else None,
        )
        if self.backend.djule_builtins:
            resolved_importables.update(self.backend.djule_builtins)

        ensure_djule_autoreload(
            settings_obj=self.backend.settings_obj,
            extra_paths=self.backend.search_paths,
        )

        if self.template_path is not None:
            renderer = DjuleRenderer.from_file(
                self.template_path,
                component_registry=self.backend.component_registry,
                importables=resolved_importables,
                search_paths=self.backend.search_paths,
            )
        else:
            renderer = DjuleRenderer.from_source(
                self.source or "",
                component_registry=self.backend.component_registry,
                importables=resolved_importables,
                search_paths=self.backend.search_paths,
            )

        return renderer.render(
            component_name=self.backend.component_name,
            props=render_props,
            ambient_props=ambient_props,
        )


class DjuleTemplates(BaseEngine):  # pragma: no cover - covered via Django integration tests
    app_dirname = "templates"

    def __init__(self, params):
        if django_settings is None or Origin is None or Engine is None:
            raise RuntimeError("Django integration requires Django to be installed")

        params = params.copy()
        options = params.pop("OPTIONS", {}).copy()
        self.options = options
        self._template_config = {
            "BACKEND": DJULE_TEMPLATE_BACKEND,
            "NAME": params.get("NAME"),
            "DIRS": list(params.get("DIRS", [])),
            "APP_DIRS": params.get("APP_DIRS", False),
            "OPTIONS": options,
        }
        super().__init__(params)

        self.component_name = options.get("component_name")
        self.component_registry = options.get("component_registry")
        self.djule_builtins = dict(options.get("djule_builtins") or {})
        self.include_request_prop = bool(options.get("include_request_prop", False))
        self.engine = Engine(
            dirs=list(self.dirs),
            app_dirs=self.app_dirs,
            context_processors=list(options.get("context_processors", []) or []),
            debug=bool(options.get("debug", getattr(django_settings, "DEBUG", False))),
            string_if_invalid=options.get("string_if_invalid", ""),
            file_charset=options.get("file_charset", "utf-8"),
            libraries=dict(options.get("libraries", {}) or {}),
            builtins=list(options.get("builtins", []) or []),
            autoescape=bool(options.get("autoescape", True)),
        )
        self.search_paths = [Path(path).resolve() for path in self.template_dirs]
        self.settings_obj = django_settings

    def from_string(self, template_code):
        return DjuleTemplate(
            self,
            template_name="<string>",
            origin=Origin(name="<djule:from_string>", template_name=None, loader=self),
            source=template_code,
        )

    def get_template(self, template_name):
        tried = []
        for candidate in self.iter_template_filenames(template_name):
            origin = Origin(name=candidate, template_name=template_name, loader=self)
            path = Path(candidate)
            if path.exists():
                return DjuleTemplate(
                    self,
                    template_name=template_name,
                    origin=origin,
                    template_path=path,
                )
            tried.append((origin, "Source does not exist"))

        raise TemplateDoesNotExist(template_name, tried=tried, backend=self)
