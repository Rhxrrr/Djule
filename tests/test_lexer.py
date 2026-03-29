from __future__ import annotations

import unittest

from djule.parser import DjuleLexer, LexerError, TokenType
from tests.fixture_paths import example_path


class LexerTests(unittest.TestCase):
    def lex(self, filename: str):
        source = example_path(filename).read_text()
        return DjuleLexer(source).tokenize()

    def test_simple_page_tokenizes(self):
        tokens = self.lex("01_simple_page.djule")
        token_types = [token.type for token in tokens]
        values = [token.value for token in tokens]

        self.assertIn(TokenType.DEF, token_types)
        self.assertIn(TokenType.HTML_TAG_OPEN, token_types)
        self.assertIn(TokenType.TEXT, token_types)
        self.assertIn(TokenType.LBRACE, token_types)
        self.assertIn(TokenType.RBRACE, token_types)
        self.assertIn("title", values)
        self.assertEqual(tokens[-1].type, TokenType.EOF)

    def test_component_import_tokenizes(self):
        tokens = self.lex("02_component_import.djule")
        values = [token.value for token in tokens]
        token_types = [token.type for token in tokens]

        self.assertIn(TokenType.FROM, token_types)
        self.assertIn(TokenType.IMPORT, token_types)
        self.assertIn(TokenType.COMPONENT_TAG_OPEN, token_types)
        self.assertIn("Button", values)
        self.assertIn("Continue", values)

    def test_doctype_tokenizes_as_markup_declaration(self):
        source = """def Page():
    return (
        <!doctype html>
        <html>
            <body>Hello</body>
        </html>
    )
"""

        tokens = DjuleLexer(source).tokenize()

        self.assertIn(TokenType.DECLARATION, [token.type for token in tokens])
        declaration = next(token for token in tokens if token.type == TokenType.DECLARATION)
        self.assertEqual(declaration.value, "<!doctype html>")

    def test_children_example_has_component_nodes_and_text(self):
        tokens = self.lex("03_children.djule")
        values = [token.value for token in tokens]

        self.assertIn("Section", values)
        self.assertIn("children", values)
        self.assertIn("Nested content is passed through the reserved children prop.", values)

    def test_logic_above_return_tokenizes_python_and_markup(self):
        tokens = self.lex("04_logic_above_return.djule")
        token_types = [token.type for token in tokens]
        values = [token.value for token in tokens]

        self.assertIn(TokenType.IF, token_types)
        self.assertIn(TokenType.ELSE, token_types)
        self.assertIn(TokenType.OPERATOR, token_types)
        self.assertIn("unread_count", values)
        self.assertIn("You have ", values)
        self.assertIn(" unread notifications.", values)

    def test_raises_for_unexpected_extra_indentation_inside_same_block(self):
        source = """def Page():
   greeting = "hi"
    badge = "oops"
   return (
       <p>{greeting}</p>
   )
"""

        with self.assertRaises(LexerError) as context:
            DjuleLexer(source).tokenize()

        self.assertIn("Unexpected indentation", str(context.exception))

    def test_allows_consistent_nonstandard_indentation_per_block(self):
        source = """def Page():
   greeting = "hi"
   if True:
      badge = "ok"
   return (
      <p>{greeting}</p>
   )
"""

        tokens = DjuleLexer(source).tokenize()

        self.assertEqual(tokens[-1].type, TokenType.EOF)

    def test_raises_when_block_opener_is_not_followed_by_indented_body(self):
        source = """def Page():
    if True:
    return (
        <p>Hi</p>
    )
"""

        with self.assertRaises(LexerError) as context:
            DjuleLexer(source).tokenize()

        self.assertIn("Expected indented block", str(context.exception))


if __name__ == "__main__":
    unittest.main()
