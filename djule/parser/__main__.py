from __future__ import annotations

import json
import sys
from pathlib import Path
from pprint import pprint

from djule.compiler import DjuleRenderer, RendererError

from .lexer import DjuleLexer, LexerError
from .parser import DjuleParser, ParserError
from .printer import DjulePrinter
from .tree_printer import DjuleTreePrinter


def _usage() -> str:
    return (
        "Usage: python -m djule.parser <lexer|parser|ast|ast-raw|render> <path-to-file.djule> "
        "[--component <name>] [--props '<json-object>']\n"
        "Aliases: lexer=tokens, parser=source"
    )


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] not in {"lexer", "parser", "ast", "ast-raw", "render", "tokens", "source"}:
        print(_usage())
        return 1

    mode = sys.argv[1]
    path = Path(sys.argv[2])
    component_name: str | None = None
    props: dict[str, object] = {}

    extra_args = sys.argv[3:]
    if mode != "render" and extra_args:
        print(_usage())
        return 1

    index = 0
    while index < len(extra_args):
        option = extra_args[index]
        if option == "--component":
            index += 1
            if index >= len(extra_args):
                print("Missing value for --component")
                return 1
            component_name = extra_args[index]
        elif option == "--props":
            index += 1
            if index >= len(extra_args):
                print("Missing value for --props")
                return 1
            try:
                loaded = json.loads(extra_args[index])
            except json.JSONDecodeError as exc:
                print(f"Invalid JSON for --props: {exc}")
                return 1
            if not isinstance(loaded, dict):
                print("--props must be a JSON object")
                return 1
            props = loaded
        else:
            print(f"Unknown option: {option}")
            print(_usage())
            return 1
        index += 1

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

    if mode == "render":
        try:
            print(DjuleRenderer.from_file(path).render(component_name=component_name, props=props))
        except ParserError as exc:
            print(f"Parser error: {exc}")
            return 3
        except RendererError as exc:
            print(f"Renderer error: {exc}")
            return 4
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
