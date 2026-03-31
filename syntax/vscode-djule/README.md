# Djule VS Code Language Support

This extension provides syntax highlighting, diagnostics, autocomplete, and component navigation for `.djule` files.

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

## Version 2
Version 2 turns the extension into a practical Djule authoring workflow with:
- live lexer/parser diagnostics
- Django-aware globals and builtin tag discovery
- completion dropdowns for imports, components, props, globals, and snippets
- Ctrl/Cmd-click component navigation
- improved handling for self-closing tags, multiline params, and interpolated expressions

## Install Locally
1. Open this folder in VS Code and run it in an Extension Development Host.
2. Or copy/symlink this folder into your local VS Code extensions directory.

## Live Diagnostics
- The extension starts a persistent `python -m djule.parser serve-json` process, similar to a lightweight syntax server.
- It sends the current unsaved document text over stdio requests, so diagnostics update before save without spawning a fresh Python process on every edit.
- It first uses `djule.pythonCommand` when set.
- Otherwise it asks the Python extension for the workspace-selected interpreter, then falls back to common local environment names like `.venv`, `venv`, and `env`, and only uses `python3` as a last resort.
- You can still override the interpreter explicitly with the `djule.pythonCommand` VS Code setting.
- The extension auto-detects the Djule project root by walking upward from the file until it finds the local `src/djule` package layout.
- If no local Djule source tree is found, the extension falls back to the current workspace folder so completions and diagnostics still work in apps that only consume the published `djule` package.
- If auto-detection fails, set `djule.projectRoot` to the absolute repo path.
- You can disable live checking with the `djule.liveSyntax` setting.

## Django Globals And Tags
When the selected Python environment can import your Django project, the extension now asks Djule's Python server to discover:
- globals returned by Django context processors
- simple tags that Django has registered globally through template builtins

That means names like `VITE_DEV_HOST`, `request`, or a global `vite_asset(...)` simple tag can become available in Djule diagnostics and autocomplete without manually duplicating them in VS Code settings.

The extension auto-detects `DJANGO_SETTINGS_MODULE` by walking upward from the current `.djule` file until it finds `manage.py`. If your project needs an explicit override, set `djule.djangoSettingsModule`.

## Optional Overrides
If you want to add extra globals or override the discovered schema details, you can still use `djule.globals`.

```json
{
  "djule.djangoSettingsModule": "wheelify.settings",
  "djule.globals": {
    "VITE_DEV_HOST": "Injected by a Django context processor",
    "request": {
      "detail": "Django request object",
      "members": {
        "path": "Current request path",
        "user": {
          "detail": "Authenticated user",
          "members": {
            "username": "Username",
            "is_authenticated": "Authentication flag"
          }
        }
      }
    }
  }
}
```

Discovered and configured globals do three things:
- suppress false `undefined-name` diagnostics for those top-level globals
- add top-level autocomplete for the discovered global names and simple tags
- add member autocomplete like `request.user.username` when Djule can infer nested members

## Next Improvements
- smarter highlighting for imports vs variables
- better handling for embedded logic blocks
- semantic tokens once the parser exists
