from __future__ import annotations

import sys
from pathlib import Path
from pprint import pprint

from .lexer import DjuleLexer, LexerError
from .parser import DjuleParser, ParserError
from .printer import DjulePrinter
from .tree_printer import DjuleTreePrinter


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in {"lexer", "parser", "ast", "ast-raw", "tokens", "source"}:
        print(
            "Usage: python -m djule.parser <lexer|parser|ast|ast-raw> <path-to-file.djule>\n"
            "Aliases: lexer=tokens, parser=source"
        )
        return 1

    mode = sys.argv[1]
    path = Path(sys.argv[2])

    if mode in {"lexer", "tokens"}:
        lexer = DjuleLexer.from_file(path)
        try:
            tokens = lexer.tokenize()
        except LexerError as exc:
            print(f"Lexer error: {exc}")
            return 2
        for token in tokens:
            print(token)
        return 0

    parser = DjuleParser.from_file(path)
    try:
        module = parser.parse()
    except ParserError as exc:
        print(f"Parser error: {exc}")
        return 3

    if mode == "ast-raw":
        pprint(module)
        return 0

    if mode == "ast":
        print(DjuleTreePrinter().print_module(module))
        return 0

    printer = DjulePrinter()
    print(printer.print_module(module))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
