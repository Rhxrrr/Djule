# Djule Syntax V1 Plan

## Goal
Define the first usable version of Djule's template syntax before building the parser or compiler.

## Scope
This phase is only about the template layer:

- Python-based component files
- modular HTML components
- Django request data passed as props
- logic above `return`
- embedded block-style `if`, `else`, and `for`
- HTML-like markup inside `return (...)`

Not in scope yet:

- router
- caching
- lazy loading
- TypeScript runtime
- production packaging

## Phase 1
Lock the syntax rules.

Deliverables:
- file format for `.djule`
- import rules
- component function rules
- prop and children rules
- `{expr}` output rules
- embedded block logic rules
- allowed vs disallowed Python in templates

## Phase 2
Write example templates before implementation.

Deliverables:
- simple page
- imported component
- nested components
- props
- children
- request data from Django
- logic above `return`
- embedded `if / else`
- embedded `for`

Success rule:
- if the examples feel awkward, change the syntax before building the parser

## Phase 3
Define the syntax tree.

Minimum nodes:
- module
- import
- component definition
- html element
- component element
- text node
- expression node
- block `if`
- block `else`
- block `for`
- python statement before `return`

## Phase 4
Build the smallest parser.

First supported subset:
- imports
- one component
- html tags
- component tags
- text
- `{expr}`

Do not start with full embedded logic.

## Phase 5
Add control flow.

Next parser targets:
- logic above `return`
- embedded `if / else`
- embedded `for`
- nested blocks inside markup

## Phase 6
Compile to Django-compatible output.

First target:
- compile Djule templates into something Django can render

Reason:
- faster to validate
- easier adoption
- safer than replacing Django rendering immediately

## Rules for V1
- keep syntax readable first
- prefer block syntax over one-line-heavy logic
- keep templates side-effect free
- no database access in templates
- no network or file access in templates
- keep TypeScript separate from template authoring

## First Build Order
1. Write `SYNTAX_V1.md`
2. Create an `examples/` folder
3. Lock the AST
4. Build the parser happy path
5. Add embedded block logic
6. Compile to Django-compatible output

## Open Questions
- Should embedded logic require explicit braces around the block?
- How should children be represented internally?
- Should component props support Python expressions everywhere or only in `{expr}`?
- How much normal Python should be allowed before `return` in v1?
