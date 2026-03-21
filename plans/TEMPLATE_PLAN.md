# Djule Template Plan

## Goal
Replace Django's template language with a Python-based component syntax that keeps HTML readable, supports modular UI composition, and works naturally with Django request data.

## Core Decision
Djule templates should use Python-style component files with:

- imports for reusable components
- normal Python logic before `return`
- HTML-like markup inside `return (...)`
- optional embedded block-style logic inside markup
- `{expr}` for value output
- Django-provided per-request data passed into the root component as props
- TypeScript kept separate for client-side behavior

Keep the syntax structured, safe, and compilable.

## Syntax Decision
- Support both logic above `return` and block-style embedded logic inside markup
- Prefer normal Python above `return` for reusable or complex logic
- Allow embedded `if`, `else`, and `for` blocks when keeping logic close to the UI improves readability
- Avoid one-line-heavy ternary and comprehension style as the main authoring pattern

## Template Model
- components are defined as Python functions
- components can import other components
- logic can be written before `return`
- markup is written inside `return (...)`
- markup may contain embedded block-style Python logic
- components accept props
- child content is passed like slots/children
- backend views pass request data into the root component

## Components
- Components should be reusable HTML modules
- Components should accept props
- Components should support slots or named content areas
- Components should compose other components
- Components should receive request data through props, not hidden global state

## Data Flow
- Django views pass per-request data into the root component
- Root components pass only needed data down as props
- Components can compute display logic locally before `return`
- Business logic, database access, auth, and side effects stay in backend Python
- TypeScript handles browser behavior, interactivity, routing, and lazy loading outside the template file

## Rules
- Template logic should be side-effect free
- No database queries in templates
- No network or file access in templates
- No unsafe imports in templates
- Keep templates readable and close to HTML
- Prefer reusable components over duplicated markup
- Do not force everything into embedded logic when normal Python above `return` is clearer

## First Features
1. Syntax spec for Djule templates
2. Parser for `.djule` files
3. Compiler to Django-compatible output
4. Component imports and component function syntax
5. Variables and expression output
6. Logic above `return`
7. Embedded block-style `if / else / for`
8. Props, children, and modular components
9. Django request-context integration
10. Error reporting with source locations
11. Build and watch tooling

## Example
```html
from components.ui import Button, Card

def Page(user, notifications):
    greeting = f"Hello {user.username}" if user.is_authenticated else "Hello guest"
    unread_count = len([n for n in notifications if not n.read])
    button_variant = "primary" if user.is_authenticated else "secondary"

    return (
        <Card>
            <h1>{greeting}</h1>
            if unread_count > 0:
                <p>You have {unread_count} unread notifications.</p>
            else:
                <p>No new notifications.</p>

            <div class="actions">
                for i in range(3):
                    <Button variant={button_variant}>
                        Action {i + 1}
                    </Button>
            </div>
        </Card>
    )
```
