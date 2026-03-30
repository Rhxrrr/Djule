from __future__ import annotations

from django import template


register = template.Library()


@register.simple_tag
def vite_asset(entry_name):
    return f"/static/dist/{entry_name}"


@register.simple_tag(takes_context=True)
def context_echo(context, key):
    return context.get(key, "")
