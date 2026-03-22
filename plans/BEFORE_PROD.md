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

Djule is not production-ready yet because the project still has red tests, prototype-level execution safety, and no real Django integration layer.

## Release Blockers

### 1. Restore a Green Baseline

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

Right now Djule evaluates template expressions using Python `eval` in [renderer.py](/Users/Rhxrr/Desktop/Repos/Djule/src/compiler/renderer.py).

That is acceptable only if Djule templates are treated as trusted application code.

Before production, choose one of these explicitly:

- `Trusted templates only` for v1
- `Restricted evaluator` for template expressions

Minimum v1 recommendation:

- document that Djule templates are trusted developer-authored code
- do not allow user-authored or CMS-authored Djule templates
- add a short `SECURITY.md` or security section in the syntax docs

### 3. Build the Django Integration Layer

Djule needs a real Django-facing runtime API, not just standalone renderer usage.

Missing pieces:

- `render_djule(...)` or equivalent public API
- Django settings for import roots and runtime configuration
- template loader or integration point
- example Django view usage
- integration tests that run inside Django

Exit criteria:

- a Django app can render a `.djule` page end-to-end
- import roots work consistently inside Django
- request data can be passed as root props cleanly

### 4. Harden the Cache Layer

The cache architecture is strong, but production needs stricter guarantees.

Still needed:

- atomic cache writes
- safer behavior with multiple workers/processes
- explicit tests for imported-component cache invalidation
- clear cache versioning and invalidation rules

Exit criteria:

- cache files cannot be left half-written
- changing an imported component invalidates dependent page plans
- warm-cache behavior is predictable across restarts

### 5. Improve Runtime Error Reporting

Production errors need to point back to Djule source clearly.

Still needed:

- file path in runtime render errors
- component name in runtime render errors
- line/column when expression evaluation fails
- better import resolution failure messages in production paths

Exit criteria:

- renderer errors are actionable without manual tracing
- production logs identify the failing `.djule` file and component

### 6. Package and Release the Project Properly

Before production, Djule should be installable and testable like a real library.

Still needed:

- package metadata
- install path for the Django integration
- CI pipeline
- release checklist
- versioning strategy

Exit criteria:

- Djule can be installed cleanly
- CI blocks release on failing tests
- versioned releases are possible

## Strongly Recommended Before Prod

These are not necessarily blockers for the very first private release, but they should be completed before broader production rollout.

### Benchmarks

Measure:

- cold render
- warm render
- cached entry-plan render
- import-heavy page render

### Import and Cache Regression Coverage

Add tests for:

- imported module change invalidates page plan
- renamed or deleted component imports fail cleanly
- relative import edge cases

### Production Docs

Write:

- install guide
- Django setup guide
- import rules guide
- cache behavior guide
- security model guide

## Recommended Order

1. Fix fixtures and get the full suite green.
2. Lock the security model for v1.
3. Build Django integration.
4. Harden cache writes and invalidation.
5. Improve runtime errors.
6. Add packaging, CI, and release process.

## Definition of Ready

Djule v1 should only be treated as production-ready when:

- the full test suite is green
- security expectations are documented
- Django integration exists and is tested
- cache invalidation is proven
- runtime errors are debuggable
- installation and release flow are stable
