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

    def test_relative_import_renders_from_parent_directories(self):
        html = self.render("feature/pages/deep/09_relative_imports.djule", props={"title": "Nested Djule"})
        self.assertEqual(
            html,
            '<section class="feature-card"><h1>Nested Djule</h1>'
            '<button class="feature-btn feature-btn-primary">Relative import works</button></section>',
        )

    def test_embedded_if_else_renders_inside_markup(self):
        user = SimpleNamespace(username="Rhxrr", is_authenticated=True)
        html = self.render("05_embedded_if_else.djule", props={"user": user})
        self.assertEqual(
            html,
            '<section class="card"><h1>Hello Rhxrr</h1><p>Your account is active.</p></section>',
        )

    def test_embedded_for_renders_repeated_component_markup(self):
        user = SimpleNamespace(username="Rhxrr", is_authenticated=True)
        html = self.render("06_embedded_for.djule", props={"user": user})
        self.assertEqual(
            html,
            '<section class="card"><h1>Quick actions</h1><div class="actions">'
            '<button class="btn btn-primary">Action 1</button>'
            '<button class="btn btn-primary">Action 2</button>'
            '<button class="btn btn-primary">Action 3</button>'
            "</div></section>",
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

    def test_request_props_example_renders_with_nested_imported_layout(self):
        user = SimpleNamespace(username="Rhxrr")
        notifications = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
        team = SimpleNamespace(name="Core")
        html = self.render(
            "08_django_request_props.djule",
            props={"user": user, "notifications": notifications, "team": team},
        )
        self.assertEqual(
            html,
            '<div class="page-shell"><header class="page-header"><h2>Core</h2><span>Rhxrr</span></header>'
            '<main class="page-content"><h1>Hello Rhxrr</h1><p>You are viewing the Core dashboard.</p>'
            '<p>You have 2 notifications.</p></main></div>',
        )

    def test_nested_content_requires_children_param(self):
        source = """
def Icon(name):
    return (
        <span>{name}</span>
    )

def Page():
    return (
        <Icon name="search">
            Extra
        </Icon>
    )
"""
        renderer = DjuleRenderer.from_source(source, search_paths=[EXAMPLES])
        with self.assertRaises(RendererError):
            renderer.render()

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
