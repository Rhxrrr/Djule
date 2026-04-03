from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import djule.integrations.django as django_integration
from djule.integrations.django import DJULE_TEMPLATE_BACKEND
from djule.integrations.django import (
    build_djule_context,
    discover_djule_editor_globals,
    ensure_djule_autoreload,
    get_djule_context_processors,
    get_djule_search_paths,
    get_djule_template_tag_builtins,
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
        self._cache_dir = tempfile.TemporaryDirectory()
        self._cache_env = patch.dict(os.environ, {"DJULE_CACHE_DIR": self._cache_dir.name}, clear=False)
        self._cache_env.start()
        from djule.compiler import DjuleRenderer

        DjuleRenderer.clear_caches()

    def tearDown(self):
        self._cache_env.stop()
        self._cache_dir.cleanup()

    def test_get_djule_search_paths_prefers_explicit_settings_roots(self):
        settings_obj = SimpleNamespace(DJULE_IMPORT_ROOTS=[str(ROOT / "examples"), str(ROOT)])
        paths = get_djule_search_paths(settings_obj=settings_obj)

        self.assertEqual(paths, [(ROOT / "examples").resolve(), ROOT.resolve()])

    def test_get_djule_search_paths_falls_back_to_djule_backend_dirs(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_obj = SimpleNamespace(
                TEMPLATES=[
                    {
                        "BACKEND": DJULE_TEMPLATE_BACKEND,
                        "DIRS": [tmp_dir],
                        "APP_DIRS": False,
                    }
                ]
            )

            paths = get_djule_search_paths(settings_obj=settings_obj)

        self.assertEqual(paths, [Path(tmp_dir).resolve()])

    def test_resolve_djule_template_uses_search_paths(self):
        resolved = resolve_djule_template("simple_page_01.djule", search_paths=[ROOT / "examples"])
        self.assertEqual(resolved, (ROOT / "examples" / "simple_page_01.djule").resolve())

    def test_get_djule_context_processors_ignores_non_djule_template_backends(self):
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
        )

        processors = get_djule_context_processors(settings_obj=settings_obj)

        self.assertEqual(processors, [])

    def test_get_djule_context_processors_reads_djule_backend_options(self):
        settings_obj = SimpleNamespace(
            TEMPLATES=[
                {
                    "BACKEND": DJULE_TEMPLATE_BACKEND,
                    "OPTIONS": {
                        "context_processors": [
                            "tests.test_django_integration.debug_value_processor",
                        ],
                    },
                }
            ]
        )

        processors = get_djule_context_processors(settings_obj=settings_obj)

        self.assertEqual(
            [processor.__name__ for processor in processors],
            ["csrf", "debug_value_processor"],
        )

    def test_build_djule_context_merges_processors_in_order(self):
        settings_obj = SimpleNamespace(
            TEMPLATES=[
                {
                    "BACKEND": DJULE_TEMPLATE_BACKEND,
                    "OPTIONS": {
                        "context_processors": [
                            "tests.test_django_integration.debug_value_processor",
                            "tests.test_django_integration.vite_host_processor",
                        ],
                    },
                }
            ]
        )

        context = build_djule_context(
            SimpleNamespace(path="/login/"),
            settings_obj=settings_obj,
        )

        self.assertEqual(context["debug_value"], "enabled")
        self.assertEqual(context["shared_value"], "from-vite")
        self.assertEqual(context["VITE_DEV_HOST"], "127.0.0.1")
        self.assertEqual(context["request_path"], "/login/")
        self.assertIn("csrf_token", context)

    def test_build_djule_context_rejects_non_mapping_returns(self):
        settings_obj = SimpleNamespace(TEMPLATES=[])

        with self.assertRaises(TypeError):
            build_djule_context(None, settings_obj=settings_obj, context_processors=[invalid_context_processor])

    @unittest.skipUnless(importlib.util.find_spec("django") is not None, "Django is not installed")
    def test_build_djule_context_includes_django_builtin_csrf_token(self):
        import django
        from django.conf import settings
        from django.http import HttpRequest

        if not settings.configured:
            settings.configure(
                DEBUG=True,
                SECRET_KEY="djule-test-secret",
                DEFAULT_CHARSET="utf-8",
                ALLOWED_HOSTS=["*"],
            )
            django.setup()

        context = build_djule_context(
            HttpRequest(),
            settings_obj=SimpleNamespace(
                TEMPLATES=[
                    {
                        "BACKEND": DJULE_TEMPLATE_BACKEND,
                        "DIRS": [],
                        "APP_DIRS": False,
                        "OPTIONS": {
                            "context_processors": [
                                "tests.test_django_integration.debug_value_processor",
                            ],
                        },
                    }
                ]
            ),
        )

        self.assertIn("csrf_token", context)
        self.assertIn("debug_value", context)

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

    def test_handle_djule_file_change_invalidates_changed_path_triggers_browser_reload_and_returns_true(self):
        settings_obj = SimpleNamespace(DJULE_IMPORT_ROOTS=[str(ROOT)])

        from djule.compiler import DjuleRenderer

        original_invalidate = DjuleRenderer.invalidate_path_caches
        original_trigger_browser_reload = django_integration.trigger_browser_reload
        calls = []

        try:
            DjuleRenderer.invalidate_path_caches = classmethod(lambda cls, path: calls.append(path))
            django_integration.trigger_browser_reload = lambda: calls.append("reloaded") or True
            changed_path = ROOT / "examples" / "simple_page_01.djule"
            handled = handle_djule_file_change(changed_path, settings_obj=settings_obj)
        finally:
            DjuleRenderer.invalidate_path_caches = original_invalidate
            django_integration.trigger_browser_reload = original_trigger_browser_reload

        self.assertTrue(handled)
        self.assertEqual(calls, [changed_path.resolve(), "reloaded"])

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
                            "BACKEND": DJULE_TEMPLATE_BACKEND,
                            "OPTIONS": {
                                "context_processors": [
                                    "tests.test_django_integration.debug_value_processor",
                                    "tests.test_django_integration.vite_host_processor",
                                ],
                            },
                        }
                    ],
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
                    TEMPLATES=[
                        {
                            "BACKEND": DJULE_TEMPLATE_BACKEND,
                            "OPTIONS": {
                                "context_processors": [debug_value_processor, vite_host_processor],
                            },
                        }
                    ],
                ),
            )

        self.assertEqual(html, "<main>from-props</main>")

    def test_render_djule_local_assignment_shadows_injected_global(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            template_path = Path(tmp_dir) / "shadow.djule"
            template_path.write_text(
                """def Page():
    request = "hello"
    return (
        <main>{request}</main>
    )
"""
            )

            html = render_djule(
                request=SimpleNamespace(path="/login/"),
                template_name="shadow.djule",
                settings_obj=SimpleNamespace(
                    DJULE_IMPORT_ROOTS=[tmp_dir],
                    TEMPLATES=[
                        {
                            "BACKEND": DJULE_TEMPLATE_BACKEND,
                            "OPTIONS": {
                                "context_processors": [lambda request: {"request": request}],
                            },
                        }
                    ],
                ),
            )

        self.assertEqual(html, "<main>hello</main>")

    def test_render_djule_reuses_cached_plan_on_repeated_requests(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            template_path = Path(tmp_dir) / "page.djule"
            template_path.write_text(
                """def Page():
    return (
        <main>Hello</main>
    )
"""
            )
            settings_obj = SimpleNamespace(DJULE_IMPORT_ROOTS=[tmp_dir], DEBUG=True)

            original_ensure = django_integration.ensure_djule_autoreload
            from djule.compiler import DjuleRenderer
            from djule.compiler.cache_support import DjuleCacheMixin

            original_compile = DjuleRenderer._compile_entry_plan
            original_dependency_check = DjuleCacheMixin.__dict__["_dependencies_are_current"]
            try:
                django_integration.ensure_djule_autoreload = lambda **kwargs: True
                first_html = render_djule(
                    request=None,
                    template_name="page.djule",
                    settings_obj=settings_obj,
                )
                DjuleRenderer._compile_entry_plan = lambda self, component_name: (_ for _ in ()).throw(
                    AssertionError("cached render plan should be reused")
                )
                DjuleRenderer._dependencies_are_current = staticmethod(
                    lambda dependencies: (_ for _ in ()).throw(
                        AssertionError("cached page should not be revalidated after first trust")
                    )
                )
                second_html = render_djule(
                    request=None,
                    template_name="page.djule",
                    settings_obj=settings_obj,
                )
            finally:
                django_integration.ensure_djule_autoreload = original_ensure
                DjuleRenderer._compile_entry_plan = original_compile
                DjuleRenderer._dependencies_are_current = original_dependency_check

        self.assertEqual(first_html, "<main>Hello</main>")
        self.assertEqual(second_html, "<main>Hello</main>")

    def test_render_djule_rebuilds_after_djule_file_change_invalidates_cache(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            template_path = Path(tmp_dir) / "page.djule"
            template_path.write_text(
                """def Page():
    return (
        <main>old</main>
    )
"""
            )
            settings_obj = SimpleNamespace(DJULE_IMPORT_ROOTS=[tmp_dir], DEBUG=True)

            original_ensure = django_integration.ensure_djule_autoreload
            try:
                django_integration.ensure_djule_autoreload = lambda **kwargs: True
                first_html = render_djule(
                    request=None,
                    template_name="page.djule",
                    settings_obj=settings_obj,
                )
                template_path.write_text(
                    """def Page():
    return (
        <main>new</main>
    )
"""
                )
                handled = handle_djule_file_change(template_path, settings_obj=settings_obj)
                second_html = render_djule(
                    request=None,
                    template_name="page.djule",
                    settings_obj=settings_obj,
                )
            finally:
                django_integration.ensure_djule_autoreload = original_ensure

        self.assertEqual(first_html, "<main>old</main>")
        self.assertTrue(handled)
        self.assertEqual(second_html, "<main>new</main>")

    def test_handle_djule_file_change_invalidates_only_affected_pages(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_obj = SimpleNamespace(DJULE_IMPORT_ROOTS=[tmp_dir], DEBUG=True)
            components_dir = Path(tmp_dir) / "components"
            components_dir.mkdir()
            shared_path = components_dir / "Shared.djule"
            shared_path.write_text(
                """def Shared():
    return (
        <span>shared</span>
    )
"""
            )
            page_a_path = Path(tmp_dir) / "page_a.djule"
            page_a_path.write_text(
                """from components.Shared import Shared

def Page():
    return (
        <main><Shared></Shared></main>
    )
"""
            )
            page_b_path = Path(tmp_dir) / "page_b.djule"
            page_b_path.write_text(
                """def Page():
    return (
        <aside>independent</aside>
    )
"""
            )

            from djule.compiler import DjuleRenderer
            original_ensure = django_integration.ensure_djule_autoreload
            try:
                django_integration.ensure_djule_autoreload = lambda **kwargs: True
                html_a = render_djule(
                    request=None,
                    template_name="page_a.djule",
                    settings_obj=settings_obj,
                )
                html_b = render_djule(
                    request=None,
                    template_name="page_b.djule",
                    settings_obj=settings_obj,
                )
                page_a_cache_path = DjuleRenderer._plan_cache_path(page_a_path.resolve(), "Page")
                page_b_cache_path = DjuleRenderer._plan_cache_path(page_b_path.resolve(), "Page")
                self.assertTrue(page_a_cache_path.exists())
                self.assertTrue(page_b_cache_path.exists())
                handled = handle_djule_file_change(shared_path, settings_obj=settings_obj)
            finally:
                django_integration.ensure_djule_autoreload = original_ensure

            self.assertEqual(html_a, "<main><span>shared</span></main>")
            self.assertEqual(html_b, "<aside>independent</aside>")
            self.assertTrue(handled)
            self.assertFalse(page_a_cache_path.exists())
            self.assertTrue(page_b_cache_path.exists())

    @unittest.skipUnless(importlib.util.find_spec("django") is not None, "Django is not installed")
    def test_get_djule_template_tag_builtins_discovers_global_simple_tags(self):
        settings_obj = SimpleNamespace(
            TEMPLATES=[
                {
                    "BACKEND": DJULE_TEMPLATE_BACKEND,
                    "OPTIONS": {
                        "builtins": ["tests.fixture_django_tags"],
                    },
                }
            ]
        )

        builtins = get_djule_template_tag_builtins(
            request=SimpleNamespace(path="/dashboard/"),
            base_context={"request_path": "/dashboard/"},
            settings_obj=settings_obj,
        )

        self.assertIn("vite_asset", builtins)
        self.assertIn("context_echo", builtins)
        self.assertEqual(builtins["vite_asset"]("main.js"), "/static/dist/main.js")
        self.assertEqual(builtins["context_echo"]("request_path"), "/dashboard/")

    @unittest.skipUnless(importlib.util.find_spec("django") is not None, "Django is not installed")
    def test_get_djule_template_tag_builtins_discovers_static_helper_from_builtin_library(self):
        import django
        from django.conf import settings

        if not settings.configured:
            settings.configure(
                DEBUG=True,
                SECRET_KEY="djule-test-secret",
                DEFAULT_CHARSET="utf-8",
                ALLOWED_HOSTS=["*"],
                STATIC_URL="/static/",
                INSTALLED_APPS=[],
            )
            django.setup()
        else:
            settings.STATIC_URL = "/static/"

        settings_obj = SimpleNamespace(
            TEMPLATES=[
                {
                    "BACKEND": DJULE_TEMPLATE_BACKEND,
                    "OPTIONS": {
                        "builtins": ["django.templatetags.static"],
                    },
                }
            ],
        )

        builtins = get_djule_template_tag_builtins(settings_obj=settings_obj)

        self.assertIn("static", builtins)
        self.assertEqual(builtins["static"]("svg/wheelify.svg"), "/static/svg/wheelify.svg")

    @unittest.skipUnless(importlib.util.find_spec("django") is not None, "Django is not installed")
    def test_render_djule_can_call_django_global_simple_tags(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            template_path = Path(tmp_dir) / "tag.djule"
            template_path.write_text(
                """from builtins import vite_asset, context_echo

def Page():
    return (
        <main>{vite_asset("main.js")}::{context_echo("request_path")}</main>
    )
"""
            )

            html = render_djule(
                request=SimpleNamespace(path="/assets/"),
                template_name="tag.djule",
                settings_obj=SimpleNamespace(
                    DJULE_IMPORT_ROOTS=[tmp_dir],
                    TEMPLATES=[
                        {
                            "BACKEND": DJULE_TEMPLATE_BACKEND,
                            "OPTIONS": {
                                "context_processors": [
                                    "tests.test_django_integration.vite_host_processor",
                                ],
                                "builtins": ["tests.fixture_django_tags"],
                            },
                        }
                    ],
                ),
            )

        self.assertEqual(html, "<main>/static/dist/main.js::/assets/</main>")

    @unittest.skipUnless(importlib.util.find_spec("django") is not None, "Django is not installed")
    def test_render_djule_can_call_static_from_builtin_library(self):
        import django
        from django.conf import settings

        if not settings.configured:
            settings.configure(
                DEBUG=True,
                SECRET_KEY="djule-test-secret",
                DEFAULT_CHARSET="utf-8",
                ALLOWED_HOSTS=["*"],
                STATIC_URL="/static/",
                INSTALLED_APPS=[],
            )
            django.setup()
        else:
            settings.STATIC_URL = "/static/"

        with tempfile.TemporaryDirectory() as tmp_dir:
            template_path = Path(tmp_dir) / "static.djule"
            template_path.write_text(
                """from builtins import static

def Page():
    return (
        <main>{static("svg/wheelify.svg")}</main>
    )
"""
            )

            html = render_djule(
                request=SimpleNamespace(path="/assets/"),
                template_name="static.djule",
                settings_obj=SimpleNamespace(
                    DJULE_IMPORT_ROOTS=[tmp_dir],
                    TEMPLATES=[
                        {
                            "BACKEND": DJULE_TEMPLATE_BACKEND,
                            "OPTIONS": {
                                "builtins": ["django.templatetags.static"],
                            },
                        }
                    ],
                ),
            )

        self.assertEqual(html, "<main>/static/svg/wheelify.svg</main>")

    @unittest.skipUnless(importlib.util.find_spec("django") is not None, "Django is not installed")
    def test_discover_djule_editor_globals_reads_context_processors_and_global_tags(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_obj = SimpleNamespace(
                TEMPLATES=[
                    {
                        "BACKEND": DJULE_TEMPLATE_BACKEND,
                        "DIRS": [tmp_dir],
                        "APP_DIRS": False,
                        "OPTIONS": {
                            "builtins": ["tests.fixture_django_tags"],
                            "context_processors": [
                                "django.template.context_processors.request",
                                "tests.test_django_integration.debug_value_processor",
                                "tests.test_django_integration.vite_host_processor",
                            ],
                        },
                    },
                ]
            )

            payload = discover_djule_editor_globals(settings_obj=settings_obj)
        globals_schema = payload["globals"]
        builtin_schema = payload["builtins"]

        self.assertIn("VITE_DEV_HOST", globals_schema)
        self.assertIn("request", globals_schema)
        self.assertIn("vite_asset", builtin_schema)
        self.assertIn("context_echo", builtin_schema)
        self.assertEqual(payload["searchPaths"], [str(Path(tmp_dir).resolve())])
        self.assertEqual(globals_schema["request"]["members"]["user"]["members"]["username"]["detail"], "str")
        self.assertIn("vite_asset(", builtin_schema["vite_asset"]["detail"])
        self.assertIn("context_echo(", builtin_schema["context_echo"]["detail"])

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

    @unittest.skipUnless(importlib.util.find_spec("django") is not None, "Django is not installed")
    def test_djule_backend_renders_templates_with_context_processors_and_builtins(self):
        import django
        from django.conf import settings
        from django.template.loader import get_template

        with tempfile.TemporaryDirectory() as tmp_dir:
            template_path = Path(tmp_dir) / "page.djule"
            template_path.write_text(
                """from builtins import vite_asset

def Page(title):
    return (
        <main>{title}::{debug_value}::{request_path}::{vite_asset("main.js")}</main>
    )
"""
            )

            if not settings.configured:
                settings.configure(
                    BASE_DIR=str(ROOT),
                    DEBUG=True,
                    DEFAULT_CHARSET="utf-8",
                    SECRET_KEY="djule-test-secret",
                    ALLOWED_HOSTS=["*"],
                    TEMPLATES=[],
                )
                django.setup()

            original_templates = list(getattr(settings, "TEMPLATES", []))
            settings.TEMPLATES = [
                {
                    "BACKEND": DJULE_TEMPLATE_BACKEND,
                    "NAME": "djule",
                    "DIRS": [tmp_dir],
                    "APP_DIRS": False,
                    "OPTIONS": {
                        "context_processors": [
                            "tests.test_django_integration.debug_value_processor",
                            "tests.test_django_integration.vite_host_processor",
                        ],
                        "builtins": ["tests.fixture_django_tags"],
                    },
                }
            ]

            from django.template import engines

            engines._engines.clear()
            if "templates" in engines.__dict__:
                del engines.__dict__["templates"]

            try:
                template = get_template("page.djule", using="djule")
                html = template.render(
                    {"title": "Hello Djule"},
                    request=SimpleNamespace(path="/backend/"),
                )
            finally:
                settings.TEMPLATES = original_templates
                engines._engines.clear()
                if "templates" in engines.__dict__:
                    del engines.__dict__["templates"]

        self.assertEqual(html, "<main>Hello Djule::enabled::/backend/::/static/dist/main.js</main>")
