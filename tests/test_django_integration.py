from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace

from djule.integrations.django import get_djule_search_paths, render_djule, resolve_djule_template


ROOT = Path(__file__).resolve().parent.parent


class DjangoIntegrationTests(unittest.TestCase):
    def test_get_djule_search_paths_prefers_explicit_settings_roots(self):
        settings_obj = SimpleNamespace(DJULE_IMPORT_ROOTS=[str(ROOT / "examples"), str(ROOT)])
        paths = get_djule_search_paths(settings_obj=settings_obj)

        self.assertEqual(paths, [(ROOT / "examples").resolve(), ROOT.resolve()])

    def test_resolve_djule_template_uses_search_paths(self):
        resolved = resolve_djule_template("simple_page_01.djule", search_paths=[ROOT / "examples"])
        self.assertEqual(resolved, (ROOT / "examples" / "simple_page_01.djule").resolve())

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

    @unittest.skipUnless(importlib.util.find_spec("django") is not None, "Django is not installed")
    def test_render_djule_response_returns_http_response(self):
        import django
        from django.conf import settings
        from django.http import HttpRequest

        if not settings.configured:
            settings.configure(
                BASE_DIR=str(ROOT),
                DJULE_IMPORT_ROOTS=[str(ROOT)],
                DEFAULT_CHARSET="utf-8",
                SECRET_KEY="djule-test-secret",
                ALLOWED_HOSTS=["*"],
            )
            django.setup()

        from djule.integrations.django import render_djule_response

        response = render_djule_response(
            HttpRequest(),
            "examples/simple_page_01.djule",
            props={"title": "Hello Djule"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Hello Djule", response.content.decode("utf-8"))
