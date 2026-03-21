from __future__ import annotations

import sys
from pathlib import Path

from .lexer import DjuleLexer, LexerError


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python -m djule.parser <path-to-file.djule>")
        return 1

    path = Path(sys.argv[1])
    lexer = DjuleLexer.from_file(path)

    try:
        tokens = lexer.tokenize()
    except LexerError as exc:
        print(f"Lexer error: {exc}")
        return 2

    for token in tokens:
        print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
