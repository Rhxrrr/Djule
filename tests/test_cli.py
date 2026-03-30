from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from tests.fixture_paths import EXAMPLES, example_path
ROOT = Path(__file__).resolve().parent.parent


class ParserCliTests(unittest.TestCase):
    def run_cli(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, "-m", "djule.parser", *args],
            cwd=ROOT,
            input=stdin,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def start_cli_server(self) -> subprocess.Popen[str]:
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.Popen(
            [sys.executable, "-m", "djule.parser", "serve-json"],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

    def test_check_json_succeeds_for_valid_file(self):
        result = self.run_cli("check-json", str(example_path("01_simple_page.djule")))

        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"ok": True, "diagnostics": []})

    def test_check_json_reports_syntax_error_for_unsaved_source_from_stdin(self):
        invalid_source = """def Page():
    return (
        <main>
            <h1>Mismatch</h2>
        </main>
    )
"""
        result = self.run_cli("check-json", "-", stdin=invalid_source)

        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertIn(payload["diagnostics"][0]["code"], {"lexer", "parser"})
        self.assertIn("Expected", payload["diagnostics"][0]["message"])

    def test_check_json_reports_undefined_name_in_component_scope(self):
        invalid_source = """def Page(user, notifications):
    greeting = f"Hello {user.username}" if user.is_authenticated else "Hello guest"

    if someone > 0:
        badge = <p>You have notifications.</p>
    else:
        badge = <p>No new notifications.</p>

    return (
        <main>
            <h1>{greeting}</h1>
            {badge}
        </main>
    )
"""
        result = self.run_cli("check-json", "-", stdin=invalid_source)

        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["diagnostics"][0]["code"], "semantic.undefined-name")
        self.assertIn("someone", payload["diagnostics"][0]["message"])

    def test_check_json_accepts_configured_global_names(self):
        source = """def Page():
    return (
        <main>{VITE_DEV_HOST}::{request.user.username}</main>
    )
"""
        result = self.run_cli(
            "check-json",
            "-",
            "--global-name",
            "VITE_DEV_HOST",
            "--global-name",
            "request",
            stdin=source,
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"ok": True, "diagnostics": []})

    def test_check_json_reports_unclosed_opening_tag_instead_of_attribute_error(self):
        invalid_source = """from examples.components.ui import Button, Card

def Page(user, notifications):
    greeting = f"Hello {user.username}" if user.is_authenticated else "Hello guest"
    unread_count = len([n for n in notifications if not n.read])
    button_variant = "primary" if user.is_authenticated else "secondary"

    if unread_count > 0:
        badge = <p>You have {unread_count} unread notifications.</p>
    else:
        badge = <p>No new notifications.</p>

    return (
        <Card
            <h1>{greeting}</h1>
            {badge}
            <Button variant={button_variant}>
                Open inbox
            </Button>
        </Card>
    )
"""
        result = self.run_cli("check-json", "-", stdin=invalid_source)

        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["diagnostics"][0]["code"], "lexer")
        self.assertIn("Expected > to close tag <Card>", payload["diagnostics"][0]["message"])

    def test_check_json_reports_helpful_error_for_from_alias_module_syntax(self):
        invalid_source = """from examples.components.ui as ui

def Page():
    return (
        <main></main>
    )
"""
        result = self.run_cli("check-json", "-", stdin=invalid_source)

        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["diagnostics"][0]["code"], "parser")
        self.assertIn("use 'import ... as <alias>'", payload["diagnostics"][0]["message"])

    def test_check_json_reports_undefined_namespaced_component_reference(self):
        invalid_source = """from examples.components.ui import Button, Card

def Page():
    return (
        <ui.Card>
            <Button variant="primary">
                Continue
            </Button>
        </ui.Card>
    )
"""
        result = self.run_cli("check-json", "-", stdin=invalid_source)

        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["diagnostics"][0]["code"], "semantic.undefined-component")
        self.assertIn("ui.Card", payload["diagnostics"][0]["message"])

    def test_check_json_reports_unresolved_import_for_unsaved_source(self):
        invalid_source = """import exmaples.components.ui as ui

def Page():
    return (
        <div></div>
    )
"""
        result = self.run_cli(
            "check-json",
            "-",
            "--document-path",
            str(EXAMPLES / "10_module_imports.djule"),
            stdin=invalid_source,
        )

        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["diagnostics"][0]["code"], "semantic.unresolved-import")
        self.assertIn("exmaples.components.ui", payload["diagnostics"][0]["message"])

    def test_check_json_reports_real_file_path_for_file_backed_parser_errors(self):
        invalid_path = ROOT / "tests" / "fixtures" / "tmp_invalid_for_cli.djule"
        invalid_path.write_text(
            """def Page():
    return (
        <main>
            <h1>Mismatch</h2>
        </main>
    )
"""
        )
        self.addCleanup(lambda: invalid_path.unlink(missing_ok=True))

        result = self.run_cli("check-json", str(invalid_path))

        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["diagnostics"][0]["path"], str(invalid_path.resolve()))
        self.assertIn(str(invalid_path.resolve()), payload["diagnostics"][0]["message"])

    def test_check_json_rejects_malformed_multiline_embedded_block(self):
        invalid_source = """from examples.components.ui import Card

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
        result = self.run_cli("check-json", "-", stdin=invalid_source)

        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["diagnostics"][0]["code"], "parser")
        self.assertEqual(payload["diagnostics"][0]["line"], 8)
        self.assertIn("Expected embedded block to start with 'if', 'for', or an assignment", payload["diagnostics"][0]["message"])

    def test_check_json_reports_real_file_line_for_invalid_embedded_for_block(self):
        invalid_source = """from examples.components.ui import Card

def Page(user):
    return (
        <Card>
            <h1>
                {
                    for user.is_authenticated:
                        f"Hello {user.username}"
                    else:
                        "Hello guest"
                }
            </h1>
        </Card>
    )
"""
        result = self.run_cli("check-json", "-", stdin=invalid_source)

        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["diagnostics"][0]["code"], "parser")
        self.assertEqual(payload["diagnostics"][0]["line"], 8)
        self.assertEqual(payload["diagnostics"][0]["column"], 25)
        self.assertEqual(payload["diagnostics"][0]["endColumn"], 46)
        self.assertIn("Expected 'in' after loop variable in embedded for loop", payload["diagnostics"][0]["message"])

    def test_serve_json_reuses_one_process_for_multiple_diagnostic_requests(self):
        server = self.start_cli_server()
        def cleanup_server() -> None:
            if server.poll() is None:
                server.kill()
                server.wait(timeout=5)
            for stream in (server.stdin, server.stdout, server.stderr):
                if stream is not None and not stream.closed:
                    stream.close()

        self.addCleanup(cleanup_server)

        assert server.stdin is not None
        assert server.stdout is not None

        valid_source = example_path("01_simple_page.djule").read_text()
        invalid_source = """def Page():
    return (
        <main>
            <h1>Mismatch</h2>
        </main>
    )
"""

        server.stdin.write(
            json.dumps(
                {
                    "command": "check",
                    "documentPath": str(example_path("01_simple_page.djule")),
                    "id": 1,
                    "source": valid_source,
                }
            )
            + "\n"
        )
        server.stdin.flush()
        first_payload = json.loads(server.stdout.readline())
        self.assertEqual(first_payload["id"], 1)
        self.assertTrue(first_payload["ok"])
        self.assertEqual(first_payload["diagnostics"], [])

        server.stdin.write(
            json.dumps(
                {
                    "command": "check",
                    "id": 2,
                    "source": invalid_source,
                }
            )
            + "\n"
        )
        server.stdin.flush()
        second_payload = json.loads(server.stdout.readline())
        self.assertEqual(second_payload["id"], 2)
        self.assertFalse(second_payload["ok"])
        self.assertIn(second_payload["diagnostics"][0]["code"], {"lexer", "parser"})

        server.stdin.write(json.dumps({"command": "shutdown", "id": 3}) + "\n")
        server.stdin.flush()
        shutdown_payload = json.loads(server.stdout.readline())
        self.assertEqual(shutdown_payload, {"diagnostics": [], "id": 3, "ok": True, "shutdown": True})
        self.assertEqual(server.wait(timeout=5), 0)


if __name__ == "__main__":
    unittest.main()
