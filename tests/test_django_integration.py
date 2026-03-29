from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import djule.integrations.django as django_integration
from djule.integrations.django import (
    build_djule_context,
    ensure_djule_autoreload,
    get_djule_context_processors,
    get_djule_search_paths,
    get_djule_watch_directories,
    handle_djule_file_change,
    render_djule,
    resolve_djule_template,
    watch_djule_files,
)


ROOT = Path(__file__).resolve().parent.parent


def debug_value_processor(_request):
    return {"debug_value": "enabled", "shared_value": "from-debug"}


def vite_host_processor(request):
    path_value = getattr(request, "path", "")
    return {"VITE_DEV_HOST": "127.0.0.1", "request_path": path_value, "shared_value": "from-vite"}


def invalid_context_processor(_request):
    return "not-a-mapping"


class DjangoIntegrationTests(unittest.TestCase):
    def setUp(self):
        django_integration._AUTORELOAD_CONNECTED = False

    def test_get_djule_search_paths_prefers_explicit_settings_roots(self):
        settings_obj = SimpleNamespace(DJULE_IMPORT_ROOTS=[str(ROOT / "examples"), str(ROOT)])
        paths = get_djule_search_paths(settings_obj=settings_obj)

        self.assertEqual(paths, [(ROOT / "examples").resolve(), ROOT.resolve()])

    def test_resolve_djule_template_uses_search_paths(self):
        resolved = resolve_djule_template("simple_page_01.djule", search_paths=[ROOT / "examples"])
        self.assertEqual(resolved, (ROOT / "examples" / "simple_page_01.djule").resolve())

    def test_get_djule_context_processors_reads_template_and_djule_settings(self):
        settings_obj = SimpleNamespace(
            TEMPLATES=[
                {
                    "BACKEND": "django.template.backends.django.DjangoTemplates",
                    "OPTIONS": {
                        "context_processors": [
                            "tests.test_django_integration.debug_value_processor",
                        ],
                    },
                }
            ],
            DJULE_CONTEXT_PROCESSORS=[
                vite_host_processor,
                "tests.test_django_integration.debug_value_processor",
            ],
        )

        processors = get_djule_context_processors(settings_obj=settings_obj)

        self.assertEqual(
            [processor.__name__ for processor in processors],
            ["debug_value_processor", "vite_host_processor"],
        )

    def test_build_djule_context_merges_processors_in_order(self):
        settings_obj = SimpleNamespace(
            TEMPLATES=[
                {
                    "BACKEND": "django.template.backends.django.DjangoTemplates",
                    "OPTIONS": {
                        "context_processors": [
                            "tests.test_django_integration.debug_value_processor",
                        ],
                    },
                }
            ],
            DJULE_CONTEXT_PROCESSORS=[vite_host_processor],
        )

        context = build_djule_context(
            SimpleNamespace(path="/login/"),
            settings_obj=settings_obj,
        )

        self.assertEqual(
            context,
            {
                "debug_value": "enabled",
                "shared_value": "from-vite",
                "VITE_DEV_HOST": "127.0.0.1",
                "request_path": "/login/",
            },
        )

    def test_build_djule_context_rejects_non_mapping_returns(self):
        settings_obj = SimpleNamespace(DJULE_CONTEXT_PROCESSORS=[invalid_context_processor])

        with self.assertRaises(TypeError):
            build_djule_context(None, settings_obj=settings_obj)

    def test_get_djule_watch_directories_filters_to_existing_directories(self):
        settings_obj = SimpleNamespace(
            DJULE_IMPORT_ROOTS=[str(ROOT / "examples"), str(ROOT / "examples" / "simple_page_01.djule"), str(ROOT)]
        )

        directories = get_djule_watch_directories(settings_obj=settings_obj)

        self.assertEqual(directories, [(ROOT / "examples").resolve(), ROOT.resolve()])

    def test_watch_djule_files_registers_djule_glob_for_each_directory(self):
        settings_obj = SimpleNamespace(DJULE_IMPORT_ROOTS=[str(ROOT / "examples"), str(ROOT)])

        class StubReloader:
            def __init__(self):
                self.calls = []

            def watch_dir(self, path, glob):
                self.calls.append((Path(path).resolve(), glob))

        reloader = StubReloader()
        watched = watch_djule_files(reloader, settings_obj=settings_obj)

        self.assertEqual(watched, [(ROOT / "examples").resolve(), ROOT.resolve()])
        self.assertEqual(
            reloader.calls,
            [((ROOT / "examples").resolve(), "**/*.djule"), (ROOT.resolve(), "**/*.djule")],
        )

    def test_handle_djule_file_change_clears_caches_triggers_browser_reload_and_returns_true(self):
        settings_obj = SimpleNamespace(DJULE_IMPORT_ROOTS=[str(ROOT)])

        from djule.compiler import DjuleRenderer

        original_clear_caches = DjuleRenderer.clear_caches
        original_trigger_browser_reload = django_integration.trigger_browser_reload
        calls = []

        try:
            DjuleRenderer.clear_caches = classmethod(lambda cls: calls.append("cleared"))
            django_integration.trigger_browser_reload = lambda: calls.append("reloaded") or True
            handled = handle_djule_file_change(ROOT / "examples" / "simple_page_01.djule", settings_obj=settings_obj)
        finally:
            DjuleRenderer.clear_caches = original_clear_caches
            django_integration.trigger_browser_reload = original_trigger_browser_reload

        self.assertTrue(handled)
        self.assertEqual(calls, ["cleared", "reloaded"])

    def test_handle_djule_file_change_ignores_non_djule_files(self):
        settings_obj = SimpleNamespace(DJULE_IMPORT_ROOTS=[str(ROOT)])
        handled = handle_djule_file_change(ROOT / "README.md", settings_obj=settings_obj)
        self.assertFalse(handled)

    def test_ensure_djule_autoreload_registers_once_when_debug_enabled(self):
        settings_obj = SimpleNamespace(DEBUG=True, DJULE_AUTO_RELOAD=True)
        original_register = django_integration.register_djule_autoreload
        calls = []

        try:
            django_integration.register_djule_autoreload = lambda **kwargs: calls.append(kwargs) or ("watcher", "handler")
            registered = ensure_djule_autoreload(settings_obj=settings_obj)
        finally:
            django_integration.register_djule_autoreload = original_register

        self.assertTrue(registered)
        self.assertEqual(calls, [{"settings_obj": settings_obj, "extra_paths": None}])

    def test_render_djule_uses_settings_import_roots(self):
        settings_obj = SimpleNamespace(DJULE_IMPORT_ROOTS=[str(ROOT)])
        html = render_djule(
            request=None,
            template_name="examples/02_component_import.djule",
            props={"title": "Hello Djule"},
            settings_obj=settings_obj,
        )

        self.assertEqual(
            html,
            '<section class="card"><h1>Hello Djule</h1><p>Imported components should feel natural in Djule.</p>'
            '<button class="btn btn-primary">Continue</button></section>',
        )

    def test_render_djule_includes_context_processor_values(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            template_path = Path(tmp_dir) / "globals.djule"
            template_path.write_text(
                """def Page():
    return (
        <main>{debug_value}::{request_path}::{VITE_DEV_HOST}::{shared_value}</main>
    )
"""
            )

            html = render_djule(
                request=SimpleNamespace(path="/login/"),
                template_name="globals.djule",
                settings_obj=SimpleNamespace(
                    DJULE_IMPORT_ROOTS=[tmp_dir],
                    TEMPLATES=[
                        {
                            "BACKEND": "django.template.backends.django.DjangoTemplates",
                            "OPTIONS": {
                                "context_processors": [
                                    "tests.test_django_integration.debug_value_processor",
                                ],
                            },
                        }
                    ],
                    DJULE_CONTEXT_PROCESSORS=[vite_host_processor],
                ),
            )

        self.assertEqual(html, "<main>enabled::/login/::127.0.0.1::from-vite</main>")

    def test_render_djule_props_override_context_processor_values(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            template_path = Path(tmp_dir) / "override.djule"
            template_path.write_text(
                """def Page():
    return (
        <main>{shared_value}</main>
    )
"""
            )

            html = render_djule(
                request=SimpleNamespace(path="/login/"),
                template_name="override.djule",
                props={"shared_value": "from-props"},
                settings_obj=SimpleNamespace(
                    DJULE_IMPORT_ROOTS=[tmp_dir],
                    DJULE_CONTEXT_PROCESSORS=[debug_value_processor, vite_host_processor],
                ),
            )

        self.assertEqual(html, "<main>from-props</main>")

    @unittest.skipUnless(importlib.util.find_spec("django") is not None, "Django is not installed")
    def test_render_djule_response_returns_http_response(self):
        import django
        from django.utils import autoreload
        from django.conf import settings
        from django.http import HttpRequest

        if not settings.configured:
            settings.configure(
                BASE_DIR=str(ROOT),
                DEBUG=True,
                DJULE_IMPORT_ROOTS=[str(ROOT)],
                DEFAULT_CHARSET="utf-8",
                SECRET_KEY="djule-test-secret",
                ALLOWED_HOSTS=["*"],
            )
            django.setup()
        else:
            settings.BASE_DIR = str(ROOT)
            settings.DEBUG = True
            settings.DJULE_IMPORT_ROOTS = [str(ROOT)]
            settings.DEFAULT_CHARSET = "utf-8"

        from djule.integrations.django import render_djule_response

        response = render_djule_response(
            HttpRequest(),
            "examples/simple_page_01.djule",
            props={"title": "Hello Djule"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Hello Djule", response.content.decode("utf-8"))

        self.assertTrue(django_integration._AUTORELOAD_CONNECTED)

        class StubReloader:
            def __init__(self):
                self.calls = []

            def watch_dir(self, path, glob):
                self.calls.append((Path(path).resolve(), glob))

        reloader = StubReloader()
        autoreload.autoreload_started.send(sender=reloader)
        self.assertIn((ROOT.resolve(), "**/*.djule"), reloader.calls)
        results = autoreload.file_changed.send(sender=reloader, file_path=ROOT / "examples" / "simple_page_01.djule")
        self.assertTrue(any(result for _receiver, result in results))
