# Djule AST V1

## Goal
Define the first abstract syntax tree for Djule so the parser has a clear output shape.

## Scope
This AST only covers the syntax already accepted in Djule v1:

- imports
- component definitions
- Python logic above `return`
- HTML elements
- component elements
- `{expr}` output
- embedded block-style `if`, `else`, and `for`

## Root Node

### Module
Represents one `.djule` file.

Fields:
- `type`: `"Module"`
- `imports`: list of import nodes
- `components`: list of component definition nodes

## Imports

### ImportFrom
Represents `from x import y`.

Fields:
- `type`: `"ImportFrom"`
- `module`: dotted module path as string
- `names`: list of imported names

Example:

```python
from components.ui import Button, Card
```

## Components

### ComponentDef
Represents one Djule component function.

Fields:
- `type`: `"ComponentDef"`
- `name`: component name
- `params`: list of parameter names
- `body`: list of Python statement nodes before `return`
- `return`: one markup tree root

Example:

```python
def Page(user, notifications):
    greeting = "Hello"
    return (
        <Card></Card>
    )
```

## Python Statements Above `return`

V1 does not need full Python AST support. It only needs the subset used by Djule templates.

### AssignStmt
Represents variable assignment.

Fields:
- `type`: `"AssignStmt"`
- `target`: variable name
- `value`: expression node

### IfStmt
Represents Python logic above `return`.

Fields:
- `type`: `"IfStmt"`
- `test`: expression node
- `body`: list of statement nodes
- `orelse`: list of statement nodes

### ForStmt
Represents Python logic above `return`.

Fields:
- `type`: `"ForStmt"`
- `target`: loop variable name
- `iter`: expression node
- `body`: list of statement nodes

### ExprStmt
Represents a plain Python expression statement when needed.

Fields:
- `type`: `"ExprStmt"`
- `value`: expression node

### ReturnStmt
Represents the return target of the component.

Fields:
- `type`: `"ReturnStmt"`
- `value`: markup node

## Markup Nodes

### ElementNode
Represents a native HTML element.

Fields:
- `type`: `"ElementNode"`
- `tag`: lowercase tag name
- `attributes`: list of attribute nodes
- `children`: list of markup child nodes

Example:

```html
<div class="actions">
    <p>Hello</p>
</div>
```

### ComponentNode
Represents a Djule component used in markup.

Fields:
- `type`: `"ComponentNode"`
- `name`: PascalCase component name
- `attributes`: list of attribute nodes
- `children`: list of markup child nodes

Example:

```html
<Button variant={button_variant}>
    Open inbox
</Button>
```

### AttributeNode
Represents one attribute on an element or component.

Fields:
- `type`: `"AttributeNode"`
- `name`: attribute name
- `value`: string literal or expression node

### TextNode
Represents plain text between tags.

Fields:
- `type`: `"TextNode"`
- `value`: text content

### ExpressionNode
Represents `{expr}` inside markup.

Fields:
- `type`: `"ExpressionNode"`
- `source`: original expression text

V1 note:
- the parser may keep Python expressions as source strings first
- later versions can parse these into a richer Python expression tree

## Embedded Logic Nodes

Embedded logic always appears inside surrounding braces.

Example:

```html
{
    if unread_count > 0:
        <p>You have {unread_count} notifications.</p>
    else:
        <p>No new notifications.</p>
}
```

### BlockNode
Represents one embedded logic block.

Fields:
- `type`: `"BlockNode"`
- `statements`: list of embedded statement nodes

### EmbeddedIfNode
Represents embedded `if / else`.

Fields:
- `type`: `"EmbeddedIfNode"`
- `test`: expression node
- `body`: list of markup or embedded statement nodes
- `orelse`: list of markup or embedded statement nodes

### EmbeddedForNode
Represents embedded `for`.

Fields:
- `type`: `"EmbeddedForNode"`
- `target`: loop variable name
- `iter`: expression node
- `body`: list of markup or embedded statement nodes

### EmbeddedAssignNode
Represents assignments inside embedded blocks.

Fields:
- `type`: `"EmbeddedAssignNode"`
- `target`: variable name
- `value`: expression node

## Expression Handling

For v1, expressions can be stored as source text instead of a full Python AST.

Examples:
- `{title}`
- `{user.username}`
- `{button_variant}`
- `if unread_count > 0:`
- `for i in range(3):`

Recommended v1 shape:

### PythonExpr
Fields:
- `type`: `"PythonExpr"`
- `source`: original source text

This keeps the parser simpler and lets the compiler or later parser stage handle deeper Python semantics.

## Recommended Tree Relationships

- `Module` contains `ImportFrom` and `ComponentDef`
- `ComponentDef` contains Python statements plus one `ReturnStmt`
- `ReturnStmt` points to one markup root
- markup roots may contain `TextNode`, `ExpressionNode`, `ElementNode`, `ComponentNode`, and `BlockNode`
- `BlockNode` contains embedded statements like `EmbeddedIfNode`, `EmbeddedForNode`, and `EmbeddedAssignNode`

## V1 Parser Priority
Build the AST in this order:

1. `Module`
2. `ImportFrom`
3. `ComponentDef`
4. `AssignStmt`
5. `ReturnStmt`
6. `ElementNode`
7. `ComponentNode`
8. `TextNode`
9. `ExpressionNode`
10. `BlockNode`
11. `EmbeddedIfNode`
12. `EmbeddedForNode`

## Open Notes
- keep HTML and component nodes separate
- keep text nodes explicit
- keep expressions as source strings in v1
- do not over-design Python parsing before the markup parser works
