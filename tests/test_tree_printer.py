from __future__ import annotations

import unittest
from pathlib import Path

from djule.parser import DjuleParser, DjuleTreePrinter


EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


class TreePrinterTests(unittest.TestCase):
    def render_tree(self, filename: str) -> str:
        module = DjuleParser.from_file(EXAMPLES / filename).parse()
        return DjuleTreePrinter().print_module(module)

    def test_simple_page_tree_is_hierarchical(self):
        tree = self.render_tree("01_simple_page.djule")

        self.assertIn("Module", tree)
        self.assertIn("ComponentDef name=Page params=[title]", tree)
        self.assertIn("ElementNode <main>", tree)
        self.assertIn("ExpressionNode: {title}", tree)

    def test_logic_example_tree_includes_statements_and_markup(self):
        tree = self.render_tree("04_logic_above_return.djule")

        self.assertIn("ImportFrom module=components.ui names=[Button, Card]", tree)
        self.assertIn("AssignStmt target=greeting", tree)
        self.assertIn("IfStmt", tree)
        self.assertIn("ElementNode <p>", tree)
        self.assertIn("ComponentNode <Button>", tree)


if __name__ == "__main__":
    unittest.main()
