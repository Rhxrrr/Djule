# Before Prod

This document lists the minimum work needed before Djule v1 should be considered production-ready.

## Current Reality

Djule already has:

- a lexer
- a parser
- an AST
- a renderer
- import resolution
- render-plan caching
- VS Code syntax, diagnostics, and completion support

Djule is much closer to a private trusted-template v1 now. The core runtime, packaging scaffolding, and integration surface exist, but broader rollout still needs benchmarking and more docs.

## Release Blockers

### 1. Restore a Green Baseline

Status: Completed

Before anything else, the repo needs a stable fixture set and a passing test suite.

Current issues:

- tests still expect `examples/01_simple_page.djule`
- the repo currently has [simple_page_01.djule](/Users/Rhxrr/Desktop/Repos/Djule/examples/simple_page_01.djule)
- [04_logic_above_return.djule](/Users/Rhxrr/Desktop/Repos/Djule/examples/04_logic_above_return.djule) has drifted from what parser, printer, tree, and renderer tests expect

Exit criteria:

- all example fixtures are intentional and named consistently
- all parser, renderer, printer, tree-printer, and CLI tests pass
- CI can treat red tests as a release blocker

### 2. Lock the Security Model

Status: Completed for v1 trusted-template mode

Right now Djule evaluates template expressions using Python `eval` in [renderer.py](/Users/Rhxrr/Desktop/Repos/Djule/src/compiler/renderer.py).

That is acceptable only if Djule templates are treated as trusted application code.

Before production, choose one of these explicitly:

- `Trusted templates only` for v1
- `Restricted evaluator` for template expressions

Implemented:

- [SECURITY.md](/Users/Rhxrr/Desktop/Repos/Djule/SECURITY.md)
- explicit trusted-template-only wording for v1

### 3. Build the Django Integration Layer

Status: Implemented for v1

Djule needs a real Django-facing runtime API, not just standalone renderer usage.

Implemented:

- [src/integrations/django.py](/Users/Rhxrr/Desktop/Repos/Djule/src/integrations/django.py)
- `render_djule(...)`
- `render_djule_response(...)`
- `DJULE_IMPORT_ROOTS` / `BASE_DIR` search-path integration
- integration coverage in [test_django_integration.py](/Users/Rhxrr/Desktop/Repos/Djule/tests/test_django_integration.py)

### 4. Harden the Cache Layer

Status: Completed for the current cache design

The cache architecture is strong, but production needs stricter guarantees.

Implemented:

- atomic cache writes in [cache_support.py](/Users/Rhxrr/Desktop/Repos/Djule/src/compiler/cache_support.py)
- cache versioning via `CACHE_VERSION`
- imported-component invalidation coverage in [test_renderer.py](/Users/Rhxrr/Desktop/Repos/Djule/tests/test_renderer.py)

### 5. Improve Runtime Error Reporting

Status: Implemented for the current renderer/runtime path

Production errors need to point back to Djule source clearly.

Implemented:

- file/component/line/column context in expression failures
- better import resolution errors with importer path

### 6. Package and Release the Project Properly

Status: Implemented for private-alpha distribution

Before production, Djule should be installable and testable like a real library.

Implemented:

- [pyproject.toml](/Users/Rhxrr/Desktop/Repos/Djule/pyproject.toml)
- Django extra dependency metadata
- CI workflow in [ci.yml](/Users/Rhxrr/Desktop/Repos/Djule/.github/workflows/ci.yml)
- project [README.md](/Users/Rhxrr/Desktop/Repos/Djule/README.md)
- pre-release version `0.1.0a1`

## Still Recommended Before Broader Rollout

These are no longer blockers for a private trusted-template v1, but they should be completed before broader rollout.

### Benchmarks

Status: Not implemented yet

Measure:

- cold render
- warm render
- cached entry-plan render
- import-heavy page render

### Import and Cache Regression Coverage

Status: Mostly covered, more edge cases still useful

Add tests for:

- imported module change invalidates page plan
- renamed or deleted component imports fail cleanly
- relative import edge cases

### Production Docs

Status: Partially implemented

Write:

- install guide
- Django setup guide
- import rules guide
- cache behavior guide
- security model guide

## Private V1 Ready

Djule is now in a strong position for a private trusted-template v1 when:

- the full test suite stays green
- the trusted-template security model is respected
- Django integration is exercised in your real app
- CI remains required before merges

## Remaining Nice-to-Haves

- benchmarks for cold vs warm vs cached renders
- broader Django docs
- more import-edge-case regression tests
- eventual decision on a less confusing package/module name than `src`
