from __future__ import annotations

import unittest

from src.parser import DjuleParser, DjuleTreePrinter
from tests.fixture_paths import example_path


class TreePrinterTests(unittest.TestCase):
    def render_tree(self, filename: str) -> str:
        module = DjuleParser.from_file(example_path(filename)).parse()
        return DjuleTreePrinter().print_module(module)

    def test_simple_page_tree_is_hierarchical(self):
        tree = self.render_tree("01_simple_page.djule")

        self.assertIn("Module", tree)
        self.assertIn("ComponentDef name=Page params=[title]", tree)
        self.assertIn("ElementNode <main>", tree)
        self.assertIn("ExpressionNode: {title}", tree)

    def test_logic_example_tree_includes_statements_and_markup(self):
        tree = self.render_tree("04_logic_above_return.djule")

        self.assertIn("ImportFrom module=examples.components.ui names=[Button, Card]", tree)
        self.assertIn("AssignStmt target=greeting", tree)
        self.assertIn("IfStmt", tree)
        self.assertIn("ElementNode <p>", tree)
        self.assertIn("ComponentNode <Button>", tree)

    def test_embedded_logic_tree_includes_block_nodes(self):
        tree = self.render_tree("05_embedded_if_else.djule")

        self.assertIn("BlockNode", tree)
        self.assertIn("EmbeddedIfNode", tree)
        self.assertIn("EmbeddedExprNode: f\"Hello {user.username}\"", tree)

    def test_module_import_tree_includes_namespace_import(self):
        tree = self.render_tree("10_module_imports.djule")

        self.assertIn("ImportModule module=examples.components.ui alias=ui", tree)
        self.assertIn("ComponentNode <ui.Card>", tree)


if __name__ == "__main__":
    unittest.main()
