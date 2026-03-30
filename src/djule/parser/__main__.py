from __future__ import annotations

import json
import sys
from pathlib import Path
from pprint import pprint
from types import SimpleNamespace

from djule.compiler import DjuleRenderer, RendererError
from djule.integrations.django import discover_djule_editor_globals

from .analyzer import DjuleAnalyzer
from .lexer import DjuleLexer, LexerError
from .parser import DjuleParser, ParserError
from .printer import DjulePrinter
from .tree_printer import DjuleTreePrinter


def _usage() -> str:
    """Return the CLI usage string shared by parser entrypoint errors."""
    return (
        "Usage: python -m djule.parser <lexer|parser|ast|ast-raw|render|check-json> <path-to-file.djule|-> "
        "[--component <name>] [--props '<json-object>'] [--search-path <dir>] [--document-path <file>] "
        "[--global-name <name>]\n"
        "       python -m djule.parser serve-json\n"
        "Aliases: lexer=tokens, parser=source"
    )


def _coerce_cli_value(value: object) -> object:
    """Convert decoded JSON props into attribute-friendly namespaces recursively."""
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _coerce_cli_value(inner) for key, inner in value.items()})
    if isinstance(value, list):
        return [_coerce_cli_value(item) for item in value]
    return value


def _emit_check_json_result(*, ok: bool, diagnostics: list[dict[str, object]]) -> int:
    """Print machine-readable diagnostics JSON and return the matching exit code."""
    print(json.dumps({"ok": ok, "diagnostics": diagnostics}, separators=(",", ":"), sort_keys=True))
    return 0 if ok else 2


def _diagnostic_path(path_arg: str, path: Path, document_path: Path | None = None) -> str | None:
    """Return the most useful resolved path for diagnostics output."""
    if path_arg != "-":
        return str(path.resolve())
    if document_path is not None:
        return str(document_path.resolve())
    return None


def _check_json_payload(
    *,
    path_arg: str,
    path: Path,
    document_path: Path | None = None,
    search_paths: list[Path] | None = None,
    source_text: str | None = None,
    global_names: list[str] | None = None,
) -> dict[str, object]:
    """Build machine-readable diagnostics for one Djule source input."""
    diagnostic_path = _diagnostic_path(path_arg, path, document_path)
    try:
        parser = DjuleParser.from_source(source_text or "") if source_text is not None else DjuleParser.from_file(path)
        module = parser.parse()
    except LexerError as exc:
        diagnostic = {
            "code": "lexer",
            "column": exc.column,
            "line": exc.line,
            "message": str(exc),
            "severity": "error",
        }
        if exc.path:
            diagnostic["path"] = exc.path
        return {"ok": False, "diagnostics": [diagnostic]}
    except ParserError as exc:
        diagnostic = {
            "code": "parser",
            "column": exc.token.column,
            "line": exc.token.line,
            "message": str(exc),
            "severity": "error",
        }
        if exc.end_column is not None:
            diagnostic["endColumn"] = exc.end_column
        if exc.path:
            diagnostic["path"] = exc.path
        return {"ok": False, "diagnostics": [diagnostic]}

    analyzer_document_path = document_path or (path if path_arg != "-" else None)
    diagnostics: list[dict[str, object]] = []
    for diagnostic in DjuleAnalyzer().analyze(
        module,
        document_path=analyzer_document_path,
        global_names=global_names,
        search_paths=search_paths or None,
    ):
        payload = {
            "code": diagnostic.code,
            "column": diagnostic.column,
            "endColumn": diagnostic.end_column,
            "line": diagnostic.line,
            "message": diagnostic.message,
            "severity": diagnostic.severity,
        }
        if diagnostic_path is not None:
            payload["path"] = diagnostic_path
        diagnostics.append(payload)
    return {"ok": not diagnostics, "diagnostics": diagnostics}


def _emit_protocol_message(payload: dict[str, object]) -> None:
    """Write one JSON protocol message as a single line to stdout."""
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True), flush=True)


def _server_error_payload(message: str, *, request_id: object = None) -> dict[str, object]:
    """Format a protocol-level server error using the normal diagnostics shape."""
    payload: dict[str, object] = {
        "diagnostics": [
            {
                "code": "server",
                "column": 1,
                "line": 1,
                "message": message,
                "severity": "error",
            }
        ],
        "id": request_id,
        "ok": False,
    }
    return payload


def _discover_editor_globals_payload(
    *,
    document_path: Path | None = None,
    workspace_path: Path | None = None,
    settings_module: str | None = None,
) -> dict[str, object]:
    """Discover Django-backed globals for editor diagnostics and autocomplete."""
    try:
        globals_payload = discover_djule_editor_globals(
            document_path=document_path,
            workspace_path=workspace_path,
            settings_module=settings_module,
        )
    except Exception:
        globals_payload = {}
    return {"globals": globals_payload, "ok": True}


def _serve_json() -> int:
    """Run a long-lived newline-delimited JSON diagnostics server over stdio."""
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            _emit_protocol_message(
                _server_error_payload(f"Invalid JSON request: {exc.msg}")
            )
            continue

        if not isinstance(request, dict):
            _emit_protocol_message(_server_error_payload("Protocol request must be a JSON object"))
            continue

        request_id = request.get("id")
        command = request.get("command", "check")
        document_path_value = request.get("documentPath")
        document_path = Path(document_path_value) if isinstance(document_path_value, str) and document_path_value else None

        if command == "shutdown":
            _emit_protocol_message({"diagnostics": [], "id": request_id, "ok": True, "shutdown": True})
            return 0

        workspace_path_value = request.get("workspacePath")
        workspace_path = Path(workspace_path_value) if isinstance(workspace_path_value, str) and workspace_path_value else None
        settings_module = request.get("settingsModule") if isinstance(request.get("settingsModule"), str) else None

        if command == "discover-django":
            payload = _discover_editor_globals_payload(
                document_path=document_path,
                workspace_path=workspace_path,
                settings_module=settings_module,
            )
            payload["id"] = request_id
            _emit_protocol_message(payload)
            continue

        if command != "check":
            _emit_protocol_message(
                _server_error_payload(f"Unsupported protocol command: {command}", request_id=request_id)
            )
            continue

        source_text = request.get("source")
        if not isinstance(source_text, str):
            _emit_protocol_message(
                _server_error_payload("Protocol request field 'source' must be a string", request_id=request_id)
            )
            continue

        search_path_values = request.get("searchPaths")
        if search_path_values is None:
            search_paths = []
        elif isinstance(search_path_values, list) and all(isinstance(value, str) for value in search_path_values):
            search_paths = [Path(value) for value in search_path_values]
        else:
            _emit_protocol_message(
                _server_error_payload(
                    "Protocol request field 'searchPaths' must be an array of strings",
                    request_id=request_id,
                )
            )
            continue

        global_name_values = request.get("globals")
        if global_name_values is None:
            global_names = []
        elif isinstance(global_name_values, list) and all(isinstance(value, str) for value in global_name_values):
            global_names = global_name_values
        else:
            _emit_protocol_message(
                _server_error_payload(
                    "Protocol request field 'globals' must be an array of strings",
                    request_id=request_id,
                )
            )
            continue

        path_arg_value = request.get("path")
        path_arg = path_arg_value if isinstance(path_arg_value, str) and path_arg_value else "-"
        payload = _check_json_payload(
            path_arg=path_arg,
            path=Path(path_arg),
            document_path=document_path,
            global_names=global_names,
            search_paths=search_paths,
            source_text=source_text,
        )
        payload["id"] = request_id
        _emit_protocol_message(payload)

    return 0


def main() -> int:
    """Run the Djule parser CLI in lexer, AST, render, or diagnostics mode."""
    supported_modes = {"lexer", "parser", "ast", "ast-raw", "render", "check-json", "serve-json", "tokens", "source"}
    if len(sys.argv) < 2 or sys.argv[1] not in supported_modes:
        print(_usage())
        return 1

    mode = sys.argv[1]
    if mode == "serve-json":
        if len(sys.argv) != 2:
            print(_usage())
            return 1
        return _serve_json()

    if len(sys.argv) < 3:
        print(_usage())
        return 1

    path_arg = sys.argv[2]
    path = Path(path_arg)
    component_name: str | None = None
    props: dict[str, object] = {}
    search_paths: list[Path] = []
    document_path: Path | None = None
    global_names: list[str] = []

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
        elif option == "--global-name":
            index += 1
            if index >= len(extra_args):
                print("Missing value for --global-name")
                return 1
            global_names.append(extra_args[index])
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
        payload = _check_json_payload(
            path_arg=path_arg,
            path=path,
            document_path=document_path,
            global_names=global_names,
            search_paths=search_paths,
            source_text=sys.stdin.read() if path_arg == "-" else None,
        )
        return _emit_check_json_result(ok=bool(payload["ok"]), diagnostics=list(payload["diagnostics"]))

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
