from __future__ import annotations

import json
import sys
from pathlib import Path
from pprint import pprint
from types import SimpleNamespace

from src.compiler import DjuleRenderer, RendererError

from .analyzer import DjuleAnalyzer
from .lexer import DjuleLexer, LexerError
from .parser import DjuleParser, ParserError
from .printer import DjulePrinter
from .tree_printer import DjuleTreePrinter


def _usage() -> str:
    return (
        "Usage: python -m src.parser <lexer|parser|ast|ast-raw|render|check-json> <path-to-file.djule|-> "
        "[--component <name>] [--props '<json-object>'] [--search-path <dir>] [--document-path <file>]\n"
        "Aliases: lexer=tokens, parser=source"
    )


def _coerce_cli_value(value: object) -> object:
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _coerce_cli_value(inner) for key, inner in value.items()})
    if isinstance(value, list):
        return [_coerce_cli_value(item) for item in value]
    return value


def _emit_check_json_result(*, ok: bool, diagnostics: list[dict[str, object]]) -> int:
    print(json.dumps({"ok": ok, "diagnostics": diagnostics}, separators=(",", ":"), sort_keys=True))
    return 0 if ok else 2


def main() -> int:
    supported_modes = {"lexer", "parser", "ast", "ast-raw", "render", "check-json", "tokens", "source"}
    if len(sys.argv) < 3 or sys.argv[1] not in supported_modes:
        print(_usage())
        return 1

    mode = sys.argv[1]
    path_arg = sys.argv[2]
    path = Path(path_arg)
    component_name: str | None = None
    props: dict[str, object] = {}
    search_paths: list[Path] = []
    document_path: Path | None = None

    extra_args = sys.argv[3:]
    if mode not in {"render", "check-json"} and extra_args:
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
            props = {key: _coerce_cli_value(value) for key, value in loaded.items()}
        elif option == "--search-path":
            index += 1
            if index >= len(extra_args):
                print("Missing value for --search-path")
                return 1
            search_paths.append(Path(extra_args[index]))
        elif option == "--document-path":
            index += 1
            if index >= len(extra_args):
                print("Missing value for --document-path")
                return 1
            document_path = Path(extra_args[index])
        else:
            print(f"Unknown option: {option}")
            print(_usage())
            return 1
        index += 1

    if mode in {"lexer", "tokens"}:
        try:
            lexer = DjuleLexer(sys.stdin.read()) if path_arg == "-" else DjuleLexer.from_file(path)
            tokens = lexer.tokenize()
        except LexerError as exc:
            print(f"Lexer error: {exc}")
            return 2
        for token in tokens:
            print(token)
        return 0

    if mode == "check-json":
        try:
            parser = DjuleParser.from_source(sys.stdin.read()) if path_arg == "-" else DjuleParser.from_file(path)
            module = parser.parse()
        except LexerError as exc:
            return _emit_check_json_result(
                ok=False,
                diagnostics=[
                    {
                        "code": "lexer",
                        "column": exc.column,
                        "line": exc.line,
                        "message": exc.message,
                        "severity": "error",
                    }
                ],
            )
        except ParserError as exc:
            diagnostic = {
                "code": "parser",
                "column": exc.token.column,
                "line": exc.token.line,
                "message": exc.message,
                "severity": "error",
            }
            if exc.end_column is not None:
                diagnostic["endColumn"] = exc.end_column
            return _emit_check_json_result(
                ok=False,
                diagnostics=[diagnostic],
            )
        analyzer_document_path = document_path or (path if path_arg != "-" else None)
        diagnostics = [
            {
                "code": diagnostic.code,
                "column": diagnostic.column,
                "endColumn": diagnostic.end_column,
                "line": diagnostic.line,
                "message": diagnostic.message,
                "severity": diagnostic.severity,
            }
            for diagnostic in DjuleAnalyzer().analyze(
                module,
                document_path=analyzer_document_path,
                search_paths=search_paths or None,
            )
        ]
        return _emit_check_json_result(ok=not diagnostics, diagnostics=diagnostics)

    if mode == "render":
        try:
            renderer = DjuleRenderer.from_file(path, search_paths=search_paths or None)
            print(renderer.render(component_name=component_name, props=props))
        except LexerError as exc:
            print(f"Lexer error: {exc}")
            return 2
        except ParserError as exc:
            print(f"Parser error: {exc}")
            return 3
        except RendererError as exc:
            print(f"Renderer error: {exc}")
            return 4
        return 0

    try:
        parser = DjuleParser.from_source(sys.stdin.read()) if path_arg == "-" else DjuleParser.from_file(path)
        module = parser.parse()
    except LexerError as exc:
        print(f"Lexer error: {exc}")
        return 2
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
