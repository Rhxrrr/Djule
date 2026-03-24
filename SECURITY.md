# Security Model

## Trusted Templates Only

Djule templates are trusted application code.

That means:

- `.djule` files are intended to live in the source repository
- they are authored and reviewed by developers
- they are not intended to be edited by end users, CMS users, or untrusted third parties

## Why

Djule evaluates template expressions as Python during rendering.

Examples like:

```python
{user.username}
{len(notifications)}
```

are executed as Python expressions, not as a sandboxed mini-language.

## V1 Rule

For Djule v1:

- do use Djule for developer-authored server templates
- do not allow user-authored or CMS-authored Djule templates
- do not treat Djule as a sandboxed template language

If Djule ever needs to support untrusted authors, it will need a restricted evaluator instead of the current trusted-code model.
