# Djule VS Code Language Support

This extension provides syntax highlighting and live syntax diagnostics for `.djule` files.

## Included
- `.djule` language registration
- line comments with `#`
- bracket pairing
- live lexer/parser diagnostics while editing
- completion dropdowns for Djule keywords, snippets, component tags, imported module namespaces, and component props
- import-line completions for `from ... import ...` and `import ... as ...`
- TextMate grammar for:
  - Python-style imports and `def`
  - Djule control-flow keywords
  - HTML tags
  - component tags
  - `{expr}` interpolation
  - strings, numbers, and basic punctuation

## Current Goal
Make the v1 examples readable in VS Code before building parser-aware semantic highlighting.

## Install Locally
1. Open this folder in VS Code and run it in an Extension Development Host.
2. Or copy/symlink this folder into your local VS Code extensions directory.

## Live Diagnostics
- The extension runs `python -m djule.parser check-json -` behind the scenes.
- It sends the current unsaved document text over stdin, so diagnostics update before save.
- It first uses `djule.pythonCommand` when set.
- Otherwise it asks the Python extension for the workspace-selected interpreter, then falls back to common local environment names like `.venv`, `venv`, and `env`, and only uses `python3` as a last resort.
- You can still override the interpreter explicitly with the `djule.pythonCommand` VS Code setting.
- The extension auto-detects the Djule project root by walking upward from the file until it finds the local `src/djule` package layout.
- If no local Djule source tree is found, the extension falls back to the current workspace folder so completions and diagnostics still work in apps that only consume the published `djule` package.
- If auto-detection fails, set `djule.projectRoot` to the absolute repo path.
- You can disable live checking with the `djule.liveSyntax` setting.

## Next Improvements
- smarter highlighting for imports vs variables
- better handling for embedded logic blocks
- semantic tokens once the parser exists
