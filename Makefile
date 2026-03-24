.PHONY: install-dev test check build clean clean-cache

PYTHON ?= .venv/bin/python
NODE ?= node

install-dev:
	$(PYTHON) -m pip install --no-build-isolation -e '.[dev]'

test:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s tests -v

check: test
	$(NODE) --check syntax/vscode-djule/extension.js
	$(NODE) --check syntax/vscode-djule/lib/constants.js
	$(NODE) --check syntax/vscode-djule/lib/runtime.js
	$(NODE) --check syntax/vscode-djule/lib/symbols.js
	$(NODE) --check syntax/vscode-djule/lib/completions.js
	$(NODE) --check syntax/vscode-djule/lib/diagnostics.js

build:
	$(PYTHON) -m pip wheel --no-build-isolation . -w dist

clean:
	rm -rf build dist src/*.egg-info .pytest_cache .mypy_cache .ruff_cache htmlcov
	find . -type d -name '__pycache__' -prune -exec rm -rf {} +

clean-cache:
	rm -rf .djule-cache
