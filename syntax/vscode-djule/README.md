# Djule VS Code Language Support

This extension provides first-pass syntax highlighting for `.djule` files.

## Included
- `.djule` language registration
- line comments with `#`
- bracket pairing
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

## Next Improvements
- smarter highlighting for imports vs variables
- better handling for embedded logic blocks
- semantic tokens once the parser exists
