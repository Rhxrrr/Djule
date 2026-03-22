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
- The extension runs `python3 -m djule.parser check-json -` behind the scenes.
- It sends the current unsaved document text over stdin, so diagnostics update before save.
- You can override the Python command with the `djule.pythonCommand` VS Code setting.
- The extension auto-detects the Djule project root by walking upward from the file until it finds the local `djule` package.
- If auto-detection fails, set `djule.projectRoot` to the absolute repo path.
- You can disable live checking with the `djule.liveSyntax` setting.

## Next Improvements
- smarter highlighting for imports vs variables
- better handling for embedded logic blocks
- semantic tokens once the parser exists
