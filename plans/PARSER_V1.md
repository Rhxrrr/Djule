# Djule Parser V1

## Goal
Build the first Djule parser that can read simple `.djule` files and turn them into the AST defined in [AST_V1.md](/Users/Rhxrr/Desktop/Repos/Djule/plans/AST_V1.md).

## What A Parser Does
A parser takes source code as input and turns it into a structured tree.

Example:

```html
<h1>{title}</h1>
```

The parser should understand that this is not just text. It is:
- an `h1` element
- containing one expression node
- where the expression source is `title`

That structured result is what later lets us:
- validate syntax
- show better errors
- compile Djule into Django-compatible output

## Why We Need This Step
Djule looks similar to Python, but it is not valid Python.

This part:

```html
return (
    <Card>
        <h1>{title}</h1>
    </Card>
)
```

cannot be understood by Python directly.

So we need our own parser that understands:
- Python-like parts
- HTML-like parts
- the places where they mix

## V1 Philosophy
Keep parser v1 small.

Do not try to solve the whole language at once.

The first parser should only support the happy path:
- imports
- one or more component definitions
- simple Python statements above `return`
- `return (...)`
- HTML tags
- component tags
- text nodes
- `{expr}`

Do not start with embedded block logic.
That comes after the happy path works.

## What We Are Building First

### Input
A `.djule` source file.

### Output
A `Module` AST node with:
- imports
- component definitions
- return markup trees

## Main Parser Stages

### 1. Read Source
Load the file contents as text.

Why:
- everything starts from the raw file

### 2. Tokenize
Break the file into meaningful pieces called tokens.

Examples of tokens:
- `from`
- `import`
- `def`
- `Page`
- `(`
- `)`
- `<Card>`
- `<h1>`
- `{`
- `title`
- `}`

Why:
- parsing raw characters directly is much harder
- tokens make the next stage easier and more reliable

### 3. Parse Top-Level Structure
Read the file as:
- imports
- component definitions

Why:
- this gives the file a stable outer shape

### 4. Parse Python Before `return`
Inside a component, parse the Python statements that appear before markup begins.

V1 support:
- assignment
- `if`
- `for`
- plain expression statements when needed

Why:
- many examples compute values before rendering

### 5. Parse Returned Markup
When the parser reaches `return (...)`, switch into markup parsing.

Why:
- markup has very different rules from Python
- this is where HTML tags, component tags, text, and `{expr}` appear

### 6. Build AST Nodes
As each structure is understood, create the matching AST node.

Why:
- the AST is the parser's real output

## Important Beginner Concept: Lexer vs Parser

### Lexer
The lexer turns characters into tokens.

Example:

```python
def Page(user):
```

could become:
- `DEF`
- `NAME("Page")`
- `LPAREN`
- `NAME("user")`
- `RPAREN`
- `COLON`

### Parser
The parser takes those tokens and decides what they mean structurally.

Example:
- this sequence is a component definition
- `Page` is the component name
- `user` is a parameter

Why split them:
- lexing and parsing are easier to reason about separately
- debugging is much easier

## Recommended V1 Token Types

Top-level/Python tokens:
- `FROM`
- `IMPORT`
- `DEF`
- `RETURN`
- `IF`
- `ELSE`
- `FOR`
- `IN`
- `NAME`
- `STRING`
- `NUMBER`
- `LPAREN`
- `RPAREN`
- `LBRACKET`
- `RBRACKET`
- `LBRACE`
- `RBRACE`
- `COLON`
- `COMMA`
- `DOT`
- `EQUALS`
- `OPERATOR`
- `NEWLINE`
- `INDENT`
- `DEDENT`

Markup tokens:
- `TAG_OPEN`
- `TAG_CLOSE`
- `COMPONENT_OPEN`
- `COMPONENT_CLOSE`
- `ATTR_NAME`
- `TEXT`

V1 note:
- you do not need a perfect final token model on day one
- but you do need a consistent one

## Easiest Practical Strategy
Use a mixed parser with two modes:

### Python Mode
Used for:
- imports
- component definitions
- statements above `return`

### Markup Mode
Used for:
- HTML tags
- component tags
- text
- `{expr}`

Why:
- Djule is a hybrid language
- one parsing mode for everything will get messy fast

## First Supported Example Files
Parser v1 should target these first:

- [01_simple_page.djule](/Users/Rhxrr/Desktop/Repos/Djule/examples/01_simple_page.djule)
- [02_component_import.djule](/Users/Rhxrr/Desktop/Repos/Djule/examples/02_component_import.djule)
- [03_children.djule](/Users/Rhxrr/Desktop/Repos/Djule/examples/03_children.djule)
- [04_logic_above_return.djule](/Users/Rhxrr/Desktop/Repos/Djule/examples/04_logic_above_return.djule)

Do not target embedded block examples first.

## Suggested Parse Order

### Phase 1
Parse:
- `Module`
- `ImportFrom`
- `ComponentDef`

### Phase 2
Parse Python before `return`:
- `AssignStmt`
- `IfStmt`
- `ForStmt`
- `ExprStmt`

### Phase 3
Parse returned markup:
- `ElementNode`
- `ComponentNode`
- `AttributeNode`
- `TextNode`
- `ExpressionNode`

### Phase 4
Only after the above works:
- `BlockNode`
- `EmbeddedIfNode`
- `EmbeddedForNode`
- `EmbeddedAssignNode`

## Error Handling Goal
Parser v1 does not need perfect errors, but it should be able to say:
- what it expected
- what it found instead
- roughly where the error happened

Good enough examples:
- `Expected component name after def`
- `Expected closing tag </Card>`
- `Expected } to close expression`

Why:
- bad parser errors make language work miserable very quickly

## Recommended Output Shape
The parser should return AST only.

Do not mix in:
- Django output generation
- HTML rendering
- caching
- runtime behavior

Why:
- parser jobs stay focused
- compiler work becomes easier later

## V1 Non-Goals
Do not solve these yet:
- embedded block parsing
- full Python expression parsing
- semantic analysis
- type checking
- component import resolution
- execution
- compilation to Django templates

## First Implementation Order
1. Create a tokenizer
2. Parse top-level imports
3. Parse component definitions
4. Parse statements above `return`
5. Parse the returned markup tree
6. Output AST objects matching [AST_V1.md](/Users/Rhxrr/Desktop/Repos/Djule/plans/AST_V1.md)
7. Test against the first 4 example files

## Success Condition
Parser v1 is successful when it can parse the first 4 example files into AST output without trying to handle embedded block logic yet.

## Next Step After Parser V1
Once the happy path parser works:
- add embedded block parsing
- improve errors
- start planning the first compiler output format
