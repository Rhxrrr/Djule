from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from djule.compiler import DjuleRenderer, RendererError, SafeHtml
from djule.parser import DjuleParser
from tests.fixture_paths import EXAMPLES, example_path


def card_component(children: str = "") -> SafeHtml:
    return SafeHtml(f'<div class="card">{children}</div>')


def button_component(variant: str, children: str = "") -> SafeHtml:
    return SafeHtml(f'<button data-variant="{variant}">{children}</button>')


class RendererTests(unittest.TestCase):
    def setUp(self) -> None:
        self._cache_dir = tempfile.TemporaryDirectory()
        self._cache_env = patch.dict(os.environ, {"DJULE_CACHE_DIR": self._cache_dir.name}, clear=False)
        self._cache_env.start()
        DjuleRenderer.clear_caches()

    def tearDown(self) -> None:
        self._cache_env.stop()
        self._cache_dir.cleanup()

    def render(
        self,
        filename: str,
        props: dict[str, object] | None = None,
        component_registry: dict[str, object] | None = None,
        search_paths: list[Path] | None = None,
        component_name: str | None = None,
    ) -> str:
        renderer = DjuleRenderer.from_file(
            example_path(filename),
            component_registry=component_registry,
            search_paths=search_paths,
        )
        return renderer.render(component_name=component_name, props=props or {})

    def load_plan_payload(self, filename: str, component_name: str = "Page") -> dict[str, object]:
        path = example_path(filename).resolve()
        plan_path = DjuleRenderer._plan_cache_path(path, component_name)
        return json.loads(plan_path.read_text())

    def test_simple_page_renders_html_and_escapes_expression_values(self):
        html = self.render("01_simple_page.djule", props={"title": '<Djule & "HTML">'})
        self.assertEqual(
            html,
            '<main class="page"><h1>&lt;Djule &amp; &quot;HTML&quot;&gt;</h1>'
            "<p>Djule renders Python-based HTML components.</p></main>",
        )
        stats = DjuleRenderer.cache_stats()
        self.assertGreater(stats["parsed_modules"], 0)
        self.assertGreater(stats["compiled_expressions"], 0)
        self.assertGreater(stats["render_plans"], 0)

    def test_doctype_renders_without_embedded_block_workaround(self):
        source = """def Page():
    return (
        <!doctype html>
        <html>
            <body>Hello</body>
        </html>
    )
"""

        renderer = DjuleRenderer.from_source(source)
        self.assertEqual(renderer.render(), "<!doctype html><html><body>Hello</body></html>")

    def test_multiline_component_params_render_normally(self):
        source = """def Page(
    title,
    subtitle,
):
    return (
        <main>
            <h1>{title}</h1>
            <p>{subtitle}</p>
        </main>
    )
"""

        renderer = DjuleRenderer.from_source(source)
        self.assertEqual(
            renderer.render(props={"title": "Hello", "subtitle": "World"}),
            "<main><h1>Hello</h1><p>World</p></main>",
        )

    def test_local_assignment_shadows_same_named_render_prop(self):
        source = """def Page():
    request = "hello"
    return (
        <main>{request}</main>
    )
"""

        renderer = DjuleRenderer.from_source(source)
        self.assertEqual(
            renderer.render(props={"request": "from-props"}),
            "<main>hello</main>",
        )

    def test_interpolated_attribute_string_renders_dynamic_value(self):
        source = """def Page(button_class):
    return (
        <button class="btn {button_class}"></button>
    )
"""

        renderer = DjuleRenderer.from_source(source)
        self.assertEqual(
            renderer.render(props={"button_class": "primary"}),
            '<button class="btn primary"></button>',
        )

    def test_self_closing_html_and_component_tags_render(self):
        source = """def Button(variant, children):
    return (
        <button data-variant={variant}>{children}</button>
    )

def Page():
    return (
        <main>
            <img src="hero.png" />
            <Button variant="primary" />
        </main>
    )
"""

        renderer = DjuleRenderer.from_source(source)
        self.assertEqual(
            renderer.render(),
            '<main><img src="hero.png" /><button data-variant="primary"></button></main>',
        )
    def test_from_file_reuses_cached_parsed_module_when_source_is_unchanged(self):
        first = DjuleRenderer.from_file(example_path("01_simple_page.djule"))
        second = DjuleRenderer.from_file(example_path("01_simple_page.djule"))

        self.assertIs(first.module, second.module)
        self.assertEqual(DjuleRenderer.cache_stats()["parsed_modules"], 1)

    def test_from_file_reparses_when_source_file_changes(self):
        source_a = """def Page():
    return (
        <main>
            <p>First</p>
        </main>
    )
"""
        source_b = """def Page():
    return (
        <main>
            <p>Second</p>
        </main>
    )
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "page.djule"
            path.write_text(source_a)
            first = DjuleRenderer.from_file(path)
            first_html = first.render()

            path.write_text(source_b)
            second = DjuleRenderer.from_file(path)
            second_html = second.render()

        self.assertNotEqual(first.module, second.module)
        self.assertIn("First", first_html)
        self.assertIn("Second", second_html)

    def test_from_file_uses_disk_cached_module_after_memory_cache_is_cleared(self):
        renderer = DjuleRenderer.from_file(example_path("01_simple_page.djule"))
        html = renderer.render(props={"title": "Hello Djule"})
        self.assertIn("Hello Djule", html)

        DjuleRenderer.clear_caches()

        with patch.object(DjuleParser, "from_file", side_effect=AssertionError("parser should not run")):
            cached_renderer = DjuleRenderer.from_file(example_path("01_simple_page.djule"))
            cached_html = cached_renderer.render(props={"title": "Hello Again"})

        self.assertIn("Hello Again", cached_html)

    def test_render_writes_render_plan_to_disk_cache(self):
        self.render("01_simple_page.djule", props={"title": "Hello Djule"})

        plan_dir = Path(self._cache_dir.name) / "plans"
        plan_files = list(plan_dir.glob("*.json"))
        self.assertTrue(plan_files)
        self.assertEqual(len(plan_files), 1)

    def test_simple_page_plan_splits_static_prefix_and_suffix_around_expression(self):
        self.render("12_cache_demo.djule", props={"title": "Hello Djule"})

        payload = self.load_plan_payload("12_cache_demo.djule")
        page_plan = payload["plan"]
        expr_path = str(example_path("12_cache_demo.djule").resolve())
        self.assertEqual(
            page_plan["parts"],
            [
                {"type": "StaticPart", "value": '<section class="card"><h1>'},
                {
                    "type": "ExprPart",
                    "source": "title",
                    "line": 6,
                    "column": 17,
                    "source_path": expr_path,
                    "component_name": "Page",
                },
                {
                    "type": "StaticPart",
                    "value": '</h1><p>This paragraph Different is static and should be cached to disk.</p>'
                    '<section class="cache-note"><span>Static badge</span></section></section>',
                },
            ],
        )

    def test_plan_cache_updates_when_source_changes(self):
        source_a = """def Page(title):
    return (
        <main>
            <h1>{title}</h1>
            <p>First static fragment.</p>
        </main>
    )
"""
        source_b = """def Page(title):
    return (
        <main>
            <h1>{title}</h1>
            <section class="note">Second static fragment.</section>
        </main>
    )
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = (Path(tmp_dir) / "page.djule").resolve()
            path.write_text(source_a)

            first_renderer = DjuleRenderer.from_file(path)
            first_renderer.render(props={"title": "Demo"})
            first_payload = json.loads(DjuleRenderer._plan_cache_path(path, "Page").read_text())
            first_parts = first_payload["plan"]["parts"]
            self.assertEqual(first_parts[-1]["value"], "</h1><p>First static fragment.</p></main>")

            path.write_text(source_b)
            DjuleRenderer.clear_caches()

            second_renderer = DjuleRenderer.from_file(path)
            second_renderer.render(props={"title": "Demo"})
            second_payload = json.loads(DjuleRenderer._plan_cache_path(path, "Page").read_text())
            second_parts = second_payload["plan"]["parts"]
            self.assertEqual(
                second_parts[-1]["value"],
                '</h1><section class="note">Second static fragment.</section></main>',
            )

    def test_text_only_static_runs_are_not_cached_as_separate_plan_parts(self):
        html = self.render("13_multi_component_cache_demo.djule", props={"user_name": "Rhxrr"})
        self.assertIn("<h1>Hello Rhxrr</h1>", html)

        payload = self.load_plan_payload("13_multi_component_cache_demo.djule")
        page_plan = payload["plan"]
        self.assertEqual(len(page_plan["parts"]), 3)
        self.assertEqual(
            page_plan["parts"][1],
            {
                "type": "ExprPart",
                "source": "user_name",
                "line": 17,
                "column": 27,
                "source_path": str(example_path("13_multi_component_cache_demo.djule").resolve()),
                "component_name": "Page",
            },
        )

    def test_simple_helper_assignments_are_flattened_into_component_plan(self):
        self.render(
            "components/ui.djule",
            props={"variant": "primary", "children": SafeHtml("Continue")},
            component_name="Button",
        )

        payload = self.load_plan_payload("components/ui.djule", component_name="Button")
        button_plan = payload["plan"]
        self.assertFalse(button_plan["requires_runtime_body"])
        self.assertEqual(button_plan["parts"][0], {"type": "StaticPart", "value": '<button class="'})
        self.assertEqual(button_plan["parts"][1]["type"], "AttrExprPart")
        self.assertIn("btn btn-", button_plan["parts"][1]["source"])
        self.assertIn("variant", button_plan["parts"][1]["source"])
        self.assertEqual(button_plan["parts"][2], {"type": "StaticPart", "value": '">'})
        self.assertEqual(
            button_plan["parts"][3],
            {
                "type": "ExprPart",
                "source": "children",
                "line": 13,
                "column": 13,
                "source_path": str(example_path("components/ui.djule").resolve()),
                "component_name": "Button",
            },
        )
        self.assertEqual(button_plan["parts"][4], {"type": "StaticPart", "value": "</button>"})

    def test_page_render_only_persists_the_entry_component_plan(self):
        self.render("02_component_import.djule", props={"title": "Hello Djule"})

        plan_dir = Path(self._cache_dir.name) / "plans"
        plan_files = list(plan_dir.glob("*.json"))
        self.assertEqual(len(plan_files), 1)
        self.assertTrue(DjuleRenderer._plan_cache_path(example_path("02_component_import.djule").resolve(), "Page").exists())
        self.assertFalse(DjuleRenderer._plan_cache_path((EXAMPLES / "components/ui.djule").resolve(), "Card").exists())
        self.assertFalse(DjuleRenderer._plan_cache_path((EXAMPLES / "components/ui.djule").resolve(), "Button").exists())

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

    def test_module_import_renders_namespaced_components(self):
        html = self.render("10_module_imports.djule", props={"title": "Hello Djule"})
        self.assertEqual(
            html,
            '<section class="card"><h1>Hello Djule</h1><p>Module imports should feel natural too.</p>'
            '<button class="btn btn-primary">Continue</button></section>',
        )

    def test_module_import_without_alias_uses_full_namespace(self):
        source = """
import examples.components.ui

def Page(title):
    return (
        <examples.components.ui.Card>
            <h1>{title}</h1>
            <examples.components.ui.Button variant="primary">
                Continue
            </examples.components.ui.Button>
        </examples.components.ui.Card>
    )
"""
        renderer = DjuleRenderer.from_source(source)
        html = renderer.render(props={"title": "Hello Djule"})
        self.assertEqual(
            html,
            '<section class="card"><h1>Hello Djule</h1>'
            '<button class="btn btn-primary">Continue</button></section>',
        )

    def test_relative_import_renders_from_parent_directories(self):
        html = self.render("feature/pages/deep/09_relative_imports.djule", props={"title": "Nested Djule"})
        self.assertEqual(
            html,
            '<section class="feature-card"><h1>Nested Djule</h1>'
            '<button class="feature-btn feature-btn-primary">Relative import works</button></section>',
        )

    def test_absolute_import_from_nested_file_uses_python_like_import_root(self):
        html = self.render("feature/pages/deep/11_absolute_imports.djule", props={"title": "Nested Djule"})
        self.assertEqual(
            html,
            '<section class="feature-card"><h1>Nested Djule</h1>'
            '<button class="feature-btn feature-btn-primary">Absolute import works</button></section>',
        )

    def test_explicit_search_path_can_override_python_like_import_root(self):
        html = self.render(
            "feature/pages/deep/11_absolute_imports.djule",
            props={"title": "Nested Djule"},
            search_paths=[Path.cwd()],
        )
        self.assertEqual(
            html,
            '<section class="feature-card"><h1>Nested Djule</h1>'
            '<button class="feature-btn feature-btn-primary">Absolute import works</button></section>',
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

    def test_imported_component_with_embedded_block_keeps_its_prop_scope(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            components_dir = root / "components"
            components_dir.mkdir()

            (components_dir / "layout.djule").write_text(
                """def Document(body_class, children):
    return (
        {
            current_body_class = body_class
            <html>
                <body class={current_body_class}>
                    {children}
                </body>
            </html>
        }
    )
"""
            )
            (root / "page.djule").write_text(
                """from components.layout import Document

def Page():
    return (
        <Document body_class="app-shell">
            <main>Hello</main>
        </Document>
    )
"""
            )

            renderer = DjuleRenderer.from_file(root / "page.djule", search_paths=[root])
            html = renderer.render()

        self.assertEqual(html, '<html><body class="app-shell"><main>Hello</main></body></html>')

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

    def test_imported_component_change_invalidates_cached_page_plan(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            components_dir = root / "components"
            components_dir.mkdir()

            (components_dir / "ui.djule").write_text(
                """def Card(children):
    return (
        <section class="card">
            {children}
        </section>
    )
"""
            )
            (root / "page.djule").write_text(
                """from components.ui import Card

def Page():
    return (
        <Card>
            <p>Hello</p>
        </Card>
    )
"""
            )

            first_renderer = DjuleRenderer.from_file(root / "page.djule", search_paths=[root])
            first_html = first_renderer.render()
            self.assertIn('class="card"', first_html)

            time.sleep(0.01)
            (components_dir / "ui.djule").write_text(
                """def Card(children):
    return (
        <section class="card updated">
            {children}
        </section>
    )
"""
            )

            second_renderer = DjuleRenderer.from_file(root / "page.djule", search_paths=[root])
            second_html = second_renderer.render()
            self.assertIn('class="card updated"', second_html)

    def test_expression_failure_includes_runtime_context(self):
        source = """def Page(user):
    return (
        <main>
            <h1>{user.missing_name}</h1>
        </main>
    )
"""
        renderer = DjuleRenderer.from_source(source)
        with self.assertRaises(RendererError) as ctx:
            renderer.render(props={"user": SimpleNamespace(username="Rhxrr")})

        message = str(ctx.exception)
        self.assertTrue(message.startswith("component 'Page', line 4, column 17:"))
        self.assertIn("Failed to evaluate expression 'user.missing_name'", message)

    def test_missing_props_include_file_and_component_context(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "missing_props.djule"
            path.write_text(
                """def Page(title, vite_asset_url):
    return (
        <main>{title}</main>
    )
"""
            )

            renderer = DjuleRenderer.from_file(path)
            with self.assertRaises(RendererError) as ctx:
                renderer.render(props={"title": "Hello"})

        message = str(ctx.exception)
        self.assertTrue(message.startswith(f"file '{path.resolve()}', component 'Page':"))
        self.assertIn("Missing prop(s): vite_asset_url", message)

    def test_ambient_globals_are_visible_inside_local_child_components(self):
        source = """def LoginForm():
    return (
        <form>{csrf_token}</form>
    )

def Page():
    return (
        <main><LoginForm></LoginForm></main>
    )
"""

        renderer = DjuleRenderer.from_source(source)
        self.assertEqual(
            renderer.render(ambient_props={"csrf_token": "token-123"}),
            "<main><form>token-123</form></main>",
        )

    def test_ambient_globals_are_visible_inside_imported_components(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            components_dir = root / "components"
            components_dir.mkdir()

            (root / "page.djule").write_text(
                """from components.form import LoginForm

def Page():
    return (
        <main><LoginForm></LoginForm></main>
    )
"""
            )
            (components_dir / "form.djule").write_text(
                """def LoginForm():
    return (
        <form>{csrf_token}</form>
    )
"""
            )

            renderer = DjuleRenderer.from_file(root / "page.djule", search_paths=[root])
            self.assertEqual(
                renderer.render(ambient_props={"csrf_token": "token-123"}),
                "<main><form>token-123</form></main>",
            )

    def test_imported_component_keeps_its_builtin_import_scope(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            components_dir = root / "components"
            components_dir.mkdir()

            (root / "page.djule").write_text(
                """from components.layout import LoginDocument

def Page():
    return (
        <LoginDocument></LoginDocument>
    )
"""
            )
            (components_dir / "layout.djule").write_text(
                """from builtins import static

def LoginDocument():
    return (
        <main data-icon={static("svg/wheelify.svg")}></main>
    )
"""
            )

            renderer = DjuleRenderer.from_file(
                root / "page.djule",
                search_paths=[root],
                importables={"static": lambda path: f"/static/{path}"},
            )
            self.assertEqual(
                renderer.render(),
                '<main data-icon="/static/svg/wheelify.svg"></main>',
            )

    def test_inlined_imported_component_error_uses_imported_file_context(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            components_dir = root / "components"
            components_dir.mkdir()
            layout_path = components_dir / "layout.djule"

            (root / "page.djule").write_text(
                """from components.layout import LoginDocument

def Page():
    return (
        <LoginDocument></LoginDocument>
    )
"""
            )
            layout_path.write_text(
                """def LoginDocument():
    return (
        <main>{missing_name}</main>
    )
"""
            )

            renderer = DjuleRenderer.from_file(root / "page.djule", search_paths=[root])
            with self.assertRaises(RendererError) as ctx:
                renderer.render()

        message = str(ctx.exception)
        self.assertTrue(message.startswith(f"file '{layout_path.resolve()}', component 'LoginDocument', line 3, column 15:"))
        self.assertIn("Failed to evaluate expression 'missing_name'", message)

    def test_virtual_builtins_module_exposes_importable_helpers(self):
        source = """from builtins import static

def Page():
    return (
        <main>{static("svg/wheelify.svg")}</main>
    )
"""

        renderer = DjuleRenderer.from_source(source, importables={"static": lambda path: f"/static/{path}"})
        self.assertEqual(renderer.render(), "<main>/static/svg/wheelify.svg</main>")


if __name__ == "__main__":
    unittest.main()
