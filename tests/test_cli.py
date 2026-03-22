from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


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

    def test_check_json_succeeds_for_valid_file(self):
        result = self.run_cli("check-json", str(EXAMPLES / "01_simple_page.djule"))

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


if __name__ == "__main__":
    unittest.main()
