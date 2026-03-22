# Djule

Djule is a Python-first templating/runtime experiment for server-rendered UI.

It currently includes:

- a lexer and parser for `.djule` files
- a renderer with import resolution and render-plan caching
- a VS Code extension for syntax, diagnostics, and completions
- an optional Django integration layer

## Local Development

Run the Python test suite:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Run the parser CLI:

```bash
python3 -m src.parser check-json tests/fixtures/01_simple_page.djule
python3 -m src.parser render tests/fixtures/01_simple_page.djule --props '{"title":"Hello Djule"}'
```

## Django Integration

Djule ships a small Django-facing API in [src/integrations/django.py](/Users/Rhxrr/Desktop/Repos/Djule/src/integrations/django.py).

Example:

```python
from src.integrations.django import render_djule_response


def dashboard_view(request):
    return render_djule_response(
        request,
        "examples/08_django_request_props.djule",
        props={
            "user": request.user,
            "notifications": [],
            "team": {"name": "Core"},
        },
    )
```

Set `DJULE_IMPORT_ROOTS` in Django settings if you want explicit import roots. Otherwise Djule falls back to `BASE_DIR`.
