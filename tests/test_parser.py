from __future__ import annotations

import unittest

from djule.parser import DjuleParser, ParserError
from djule.parser.ast_nodes import (
    AssignStmt,
    BlockNode,
    ComponentDef,
    ComponentNode,
    DeclarationNode,
    EmbeddedExprNode,
    EmbeddedForNode,
    EmbeddedIfNode,
    ElementNode,
    ExpressionNode,
    FragmentNode,
    IfStmt,
    ImportFrom,
    ImportModule,
    Module,
    TextNode,
)
from tests.fixture_paths import example_path


class ParserTests(unittest.TestCase):
    def parse(self, filename: str) -> Module:
        return DjuleParser.from_file(example_path(filename)).parse()

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

        self.assertEqual(module.imports, [ImportFrom(module="examples.components.ui", names=["Button", "Card"])])
        component = module.components[0]
        root = component.return_stmt.value
        self.assertIsInstance(root, ComponentNode)
        self.assertEqual(root.name, "Card")
        self.assertEqual(root.children[-1].name, "Button")

    def test_relative_component_import_parses_leading_dot_syntax(self):
        module = self.parse("feature/pages/deep/09_relative_imports.djule")

        self.assertEqual(module.imports, [ImportFrom(module="...components.ui", names=["Button", "Card"])])
        root = module.components[0].return_stmt.value
        self.assertIsInstance(root, ComponentNode)
        self.assertEqual(root.name, "Card")

    def test_module_import_parses_alias_and_namespaced_component_tags(self):
        module = self.parse("10_module_imports.djule")

        self.assertEqual(module.imports, [ImportModule(module="examples.components.ui", alias="ui")])
        root = module.components[0].return_stmt.value
        self.assertIsInstance(root, ComponentNode)
        self.assertEqual(root.name, "ui.Card")
        self.assertEqual(root.children[-1].name, "ui.Button")

    def test_module_import_without_alias_keeps_full_module_namespace(self):
        source = """
import examples.components.ui

def Page():
    return (
        <examples.components.ui.Card>
            <examples.components.ui.Button variant="primary">
                Continue
            </examples.components.ui.Button>
        </examples.components.ui.Card>
    )
"""
        module = DjuleParser.from_source(source).parse()

        self.assertEqual(module.imports, [ImportModule(module="examples.components.ui", alias=None)])
        root = module.components[0].return_stmt.value
        self.assertEqual(root.name, "examples.components.ui.Card")
        self.assertEqual(root.children[0].name, "examples.components.ui.Button")

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

    def test_embedded_if_else_parses_block_node_and_embedded_if(self):
        module = self.parse("05_embedded_if_else.djule")

        root = module.components[0].return_stmt.value
        self.assertIsInstance(root, ComponentNode)

        heading = root.children[0]
        self.assertIsInstance(heading, ElementNode)
        self.assertIsInstance(heading.children[0], BlockNode)
        self.assertIsInstance(heading.children[0].statements[0], EmbeddedIfNode)
        self.assertIsInstance(heading.children[0].statements[0].body[0], EmbeddedExprNode)

        status_block = root.children[1]
        self.assertIsInstance(status_block, BlockNode)
        self.assertIsInstance(status_block.statements[0], EmbeddedIfNode)
        self.assertIsInstance(status_block.statements[0].body[0], ElementNode)

    def test_embedded_for_parses_block_node_and_embedded_for(self):
        module = self.parse("06_embedded_for.djule")

        actions = module.components[0].return_stmt.value.children[1]
        self.assertIsInstance(actions, ElementNode)
        self.assertIsInstance(actions.children[0], BlockNode)
        self.assertIsInstance(actions.children[0].statements[0], EmbeddedForNode)
        self.assertIsInstance(actions.children[0].statements[0].body[0], ComponentNode)

    def test_doctype_and_html_parse_as_fragment_return_value(self):
        source = """
def Page():
    return (
        <!doctype html>
        <html>
            <body>Hello</body>
        </html>
    )
"""
        module = DjuleParser.from_source(source).parse()

        root = module.components[0].return_stmt.value
        self.assertIsInstance(root, FragmentNode)
        self.assertIsInstance(root.children[0], DeclarationNode)
        self.assertEqual(root.children[0].value, "<!doctype html>")
        self.assertIsInstance(root.children[1], ElementNode)
        self.assertEqual(root.children[1].tag, "html")

    def test_children_attribute_is_rejected_on_component_tags(self):
        source = """
def Page():
    return (
        <Card children="Wrong">
            Fine
        </Card>
    )
"""
        with self.assertRaises(ParserError):
            DjuleParser.from_source(source).parse()

    def test_multiline_brace_block_without_if_or_for_is_rejected(self):
        source = """
from examples.components.ui import Card

def Page(user):
    return (
        <Card>
            <h1>
                {
                    user.is_authenticated:
                        f"Hello {user.username}"
                    else:
                        "Hello guest"
                }
            </h1>
        </Card>
    )
"""
        with self.assertRaises(ParserError):
            DjuleParser.from_source(source).parse()

    def test_multiline_component_params_with_trailing_comma_parse(self):
        source = """
def LoginDocument(
    doctype_html,
    title,
    description,
    children,
):
    return (
        <main>{title}</main>
    )
"""
        module = DjuleParser.from_source(source).parse()

        component = module.components[0]
        self.assertEqual(
            component.params,
            ["doctype_html", "title", "description", "children"],
        )


if __name__ == "__main__":
    unittest.main()
