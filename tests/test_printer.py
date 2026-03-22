from __future__ import annotations

import textwrap
import unittest

from src.parser import DjuleParser, DjulePrinter
from tests.fixture_paths import example_path


class PrinterTests(unittest.TestCase):
    def render(self, filename: str) -> str:
        module = DjuleParser.from_file(example_path(filename)).parse()
        return DjulePrinter().print_module(module)

    def test_simple_page_round_trips_to_expected_source(self):
        rendered = self.render("01_simple_page.djule")
        expected = textwrap.dedent(
            """\
            def Page(title):
                return (
                    <main class="page">
                        <h1>{title}</h1>
                        <p>Djule renders Python-based HTML components.</p>
                    </main>
                )"""
        )
        self.assertEqual(rendered, expected)

    def test_logic_above_return_renders_normalized_source(self):
        rendered = self.render("04_logic_above_return.djule")
        self.assertIn('greeting = f"Hello {user.username}" if user.is_authenticated else "Hello guest"', rendered)
        self.assertIn("if unread_count > 0:", rendered)
        self.assertIn("badge = <p>You have {unread_count} unread notifications.</p>", rendered)
        self.assertIn("<Button variant={button_variant}>Open inbox</Button>", rendered)

    def test_module_import_round_trips_with_alias(self):
        rendered = self.render("10_module_imports.djule")
        self.assertIn("import examples.components.ui as ui", rendered)
        self.assertIn("<ui.Card>", rendered)
        self.assertIn("<ui.Button variant=\"primary\">Continue</ui.Button>", rendered)

    def test_embedded_if_else_round_trips_with_block_syntax(self):
        rendered = self.render("05_embedded_if_else.djule")
        self.assertIn("{", rendered)
        self.assertIn("if user.is_authenticated:", rendered)
        self.assertIn('f"Hello {user.username}"', rendered)
        self.assertIn("<p>Your account is active.</p>", rendered)


if __name__ == "__main__":
    unittest.main()
