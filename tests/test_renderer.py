from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from djule.compiler import DjuleRenderer, RendererError, SafeHtml


EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def card_component(children: str = "") -> SafeHtml:
    return SafeHtml(f'<div class="card">{children}</div>')


def button_component(variant: str, children: str = "") -> SafeHtml:
    return SafeHtml(f'<button data-variant="{variant}">{children}</button>')


class RendererTests(unittest.TestCase):
    def render(
        self,
        filename: str,
        props: dict[str, object] | None = None,
        component_registry: dict[str, object] | None = None,
    ) -> str:
        renderer = DjuleRenderer.from_file(EXAMPLES / filename, component_registry=component_registry)
        return renderer.render(props=props or {})

    def test_simple_page_renders_html_and_escapes_expression_values(self):
        html = self.render("01_simple_page.djule", props={"title": '<Djule & "HTML">'})
        self.assertEqual(
            html,
            '<main class="page"><h1>&lt;Djule &amp; &quot;HTML&quot;&gt;</h1>'
            "<p>Djule renders Python-based HTML components.</p></main>",
        )

    def test_children_example_renders_internal_component_children(self):
        html = self.render("03_children.djule")
        self.assertEqual(
            html,
            '<section class="section"><h2>Overview</h2><div class="section-body">'
            "<p>Nested content is passed through the reserved children prop.</p>"
            "</div></section>",
        )

    def test_component_import_renders_with_automatic_file_import_resolution(self):
        html = self.render("02_component_import.djule", props={"title": "Hello Djule"})
        self.assertEqual(
            html,
            '<section class="card"><h1>Hello Djule</h1><p>Imported components should feel natural in Djule.</p>'
            '<button class="btn btn-primary">Continue</button></section>',
        )

    def test_logic_above_return_renders_imported_components_automatically(self):
        user = SimpleNamespace(username="Rhxrr", is_authenticated=True)
        notifications = [
            SimpleNamespace(read=False),
            SimpleNamespace(read=True),
            SimpleNamespace(read=False),
        ]
        html = self.render("04_logic_above_return.djule", props={"user": user, "notifications": notifications})
        self.assertEqual(
            html,
            '<section class="card"><h1>Hello Rhxrr</h1><p>You have 2 unread notifications.</p>'
            '<button class="btn btn-primary">Open inbox</button></section>',
        )

    def test_manual_component_registry_can_override_automatic_imports(self):
        html = self.render(
            "02_component_import.djule",
            props={"title": "Hello Djule"},
            component_registry={"Card": card_component, "Button": button_component},
        )
        self.assertEqual(
            html,
            '<div class="card"><h1>Hello Djule</h1><p>Imported components should feel natural in Djule.</p>'
            '<button data-variant="primary">Continue</button></div>',
        )

    def test_render_raises_for_missing_imported_module(self):
        source = """
from missing.ui import Button

def Page():
    return (
        <Button>
            Hi
        </Button>
    )
"""
        renderer = DjuleRenderer.from_source(source, search_paths=[EXAMPLES])
        with self.assertRaises(RendererError):
            renderer.render()


if __name__ == "__main__":
    unittest.main()
