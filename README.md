# Djule

Djule is a Python-first templating/runtime for server-rendered UI.

It currently includes:

- a lexer and parser for `.djule` files
- a renderer with import resolution and render-plan caching
- a VS Code extension for syntax, diagnostics, and completions
- an optional Django integration layer

Djule is licensed under [Apache License 2.0](/Users/Rhxrr/Desktop/Repos/Djule/LICENSE).

## Installation

Install the package locally:

```bash
python3 -m pip install .
```

Install from PyPI:

```bash
python3 -m pip install djule
```

Install with Django helpers:

```bash
python3 -m pip install '.[django]'
```

For local development:

```bash
python3 -m pip install -e '.[dev]'
```

## Local Development

Install the editable dev environment:

```bash
make install-dev
```

Run the Python test suite:

```bash
make test
```

Run the parser CLI:

```bash
python3 -m djule.parser check-json tests/fixtures/01_simple_page.djule
python3 -m djule.parser render tests/fixtures/01_simple_page.djule --props '{"title":"Hello Djule"}'
djule render tests/fixtures/01_simple_page.djule --props '{"title":"Hello Djule"}'
```

Useful maintenance commands:

```bash
make check
make build
make clean
make clean-cache
```

## Django Integration

Djule ships a small Django-facing API in [src/djule/integrations/django.py](/Users/Rhxrr/Desktop/Repos/Djule/src/djule/integrations/django.py).

Example:

```python
from djule.integrations.django import render_djule_response


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

When rendering through the Django integration, Djule also recognizes `{% csrf_token %}` inside markup and injects the request token automatically:

```python
def LoginForm():
    return (
        <form method="post">
            {% csrf_token %}
            <button type="submit">Sign in</button>
        </form>
    )
```
