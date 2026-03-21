# Djule Syntax V1

## Goal
Define the first usable Djule syntax for Python-based HTML components in Django.

## File Type
- Djule files use the `.djule` extension
- A Djule file is a Python-like module with imports, component functions, and HTML-like markup

## Core Shape
A Djule component is a Python function that returns HTML-like markup.

```html
from components.ui import Button, Card

def Page(user, notifications):
    greeting = f"Hello {user.username}" if user.is_authenticated else "Hello guest"

    return (
        <Card>
            <h1>{greeting}</h1>
        </Card>
    )
```

## V1 Rules
- use Python-style imports
- define components with `def`
- allow normal Python logic above `return`
- write markup inside `return (...)`
- allow `{expr}` for value output
- allow embedded block-style `if`, `else`, and `for` inside markup
- pass Django request data into the root component as props
- keep TypeScript in separate files for client-side behavior

## Imports
- use normal Python import syntax
- components may import other components
- imported modules and imported components are allowed at the top of the file

```html
from components.ui import Button, Card
from components.layout import PageShell
```

## Components
- components are Python functions
- component names should be PascalCase
- component inputs are props
- nested content is passed through the reserved prop name `children`
- components may render HTML tags or other components
- child content is passed between opening and closing component tags

```html
def Button(variant, children):
    return (
        <button class={variant}>
            {children}
        </button>
    )
```

## Markup
- native HTML tags use lowercase names
- Djule components use PascalCase names
- attributes use `name=value`
- Python values in attributes use `{expr}`
- text remains plain text inside tags

```html
<div class="actions">
    <Button variant={button_variant}>Save</Button>
</div>
```

## Expressions
Use `{expr}` to output Python values inside markup.

Allowed uses in v1:
- variables
- attribute access
- simple function calls
- arithmetic
- string formatting

```html
<h1>{user.username}</h1>
<p>{unread_count}</p>
<Button variant={button_variant} />
```

## Logic Above `return`
Normal Python can be written before the returned markup.

Preferred use:
- reusable logic
- computed values
- display state
- data shaping for markup

```html
def Page(user, notifications):
    greeting = f"Hello {user.username}" if user.is_authenticated else "Hello guest"
    unread_count = len([n for n in notifications if not n.read])
    button_variant = "primary" if user.is_authenticated else "secondary"

    return (
        <Card>
            <h1>{greeting}</h1>
        </Card>
    )
```

## Embedded Block Logic
Markup may contain block-style Python logic when that reads better than moving everything above `return`.

Allowed blocks in v1:
- `if`
- `else`
- `for`

```html
def Page(user, notifications):
    return (
        <Card>
            <h1>
                {
                    if user.is_authenticated:
                        f"Hello {user.username}"
                    else:
                        "Hello guest"
                }
            </h1>

            {
                unread_count = 0

                for n in notifications:
                    if not n.read:
                        unread_count += 1

                if unread_count > 0:
                    <p>You have {unread_count} unread notifications.</p>
                else:
                    <p>No new notifications.</p>
            }
        </Card>
    )
```

## Data Flow
- Django passes per-request data into the root component
- root components pass only needed props down
- v1 uses explicit prop drilling for nested components
- components should not rely on hidden global request state

Example intent:

```python
render_djule(request, "pages/dashboard.djule", {
    "user": request.user,
    "notifications": notifications,
})
```

## TypeScript Boundary
- Djule handles templates, component composition, and server-side UI logic
- TypeScript handles browser behavior, events, router behavior, lazy loading, and client runtime work
- Djule should not replace normal TypeScript files

## Restrictions
V1 templates should stay safe and predictable.

Disallow in templates:
- database queries
- network access
- file access
- unsafe imports
- side effects outside local render logic

## Style Guidance
- prefer readability over cleverness
- prefer logic above `return` when the same logic is reused
- use embedded logic when it makes UI flow easier to understand
- avoid one-line-heavy ternary or comprehension style as the default pattern

## V1 Minimum Feature Set
1. Imports
2. Component function definitions
3. HTML tags
4. Component tags
5. `{expr}` output
6. Python logic above `return`
7. Embedded block `if / else / for`
8. Props and children
9. Django request-data integration

## V1 Decisions
- embedded logic must use surrounding braces
- self-closing component tags are not supported in v1
- Python above `return` is allowed as needed and left to developer judgment
- v1 keeps explicit prop drilling instead of implicit shared context
- `children` is the reserved prop name for nested content passed between component tags
