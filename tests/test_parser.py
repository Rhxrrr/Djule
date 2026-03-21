from __future__ import annotations

import unittest
from pathlib import Path

from djule.parser import DjuleParser
from djule.parser.ast_nodes import (
    AssignStmt,
    ComponentDef,
    ComponentNode,
    ElementNode,
    ExpressionNode,
    IfStmt,
    ImportFrom,
    Module,
    TextNode,
)


EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


class ParserTests(unittest.TestCase):
    def parse(self, filename: str) -> Module:
        return DjuleParser.from_file(EXAMPLES / filename).parse()

    def test_simple_page_parses_module_and_component(self):
        module = self.parse("01_simple_page.djule")

        self.assertIsInstance(module, Module)
        self.assertEqual(module.imports, [])
        self.assertEqual(len(module.components), 1)

        component = module.components[0]
        self.assertEqual(component.name, "Page")
        self.assertEqual(component.params, ["title"])
        self.assertEqual(component.body, [])
        self.assertEqual(component.return_stmt.value.tag, "main")

    def test_component_import_parses_import_and_component_nodes(self):
        module = self.parse("02_component_import.djule")

        self.assertEqual(module.imports, [ImportFrom(module="components.ui", names=["Button", "Card"])])
        component = module.components[0]
        root = component.return_stmt.value
        self.assertIsInstance(root, ComponentNode)
        self.assertEqual(root.name, "Card")
        self.assertEqual(root.children[-1].name, "Button")

    def test_children_example_parses_nested_text(self):
        module = self.parse("03_children.djule")

        self.assertEqual(len(module.components), 2)
        page_component = module.components[1]
        root = page_component.return_stmt.value
        self.assertIsInstance(root, ComponentNode)
        self.assertEqual(root.name, "Section")
        self.assertEqual(root.attributes[0].name, "title")
        self.assertEqual(root.attributes[0].value, '"Overview"')

        paragraph = root.children[0]
        self.assertIsInstance(paragraph, ElementNode)
        self.assertEqual(paragraph.children, [TextNode("Nested content is passed through the reserved children prop.")])

    def test_logic_above_return_parses_python_body_and_markup(self):
        module = self.parse("04_logic_above_return.djule")

        component = module.components[0]
        self.assertIsInstance(component, ComponentDef)
        self.assertEqual(len(component.body), 4)
        self.assertIsInstance(component.body[0], AssignStmt)
        self.assertIsInstance(component.body[1], AssignStmt)
        self.assertIsInstance(component.body[2], AssignStmt)
        self.assertIsInstance(component.body[3], IfStmt)

        if_stmt = component.body[3]
        self.assertEqual(if_stmt.test.source, "unread_count > 0")
        self.assertIsInstance(if_stmt.body[0], AssignStmt)
        self.assertIsInstance(if_stmt.body[0].value, ElementNode)

        root = component.return_stmt.value
        self.assertIsInstance(root, ComponentNode)
        self.assertEqual(root.name, "Card")
        self.assertEqual(root.children[0], ElementNode(tag="h1", attributes=[], children=[ExpressionNode(source="greeting")]))
        self.assertEqual(root.children[1], ExpressionNode(source="badge"))
        self.assertEqual(root.children[2].name, "Button")


if __name__ == "__main__":
    unittest.main()
