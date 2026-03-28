from __future__ import annotations

from django.apps import AppConfig
from django.conf import settings

from .django import register_djule_autoreload


class DjuleIntegrationConfig(AppConfig):
    """Django app config that wires Djule development autoreload hooks."""
    name = "djule.integrations"
    verbose_name = "Djule Integrations"

    def ready(self) -> None:
        """Register Djule autoreload hooks when Django starts in debug mode."""
        if not getattr(settings, "DEBUG", False):
            return

        if getattr(settings, "DJULE_AUTO_RELOAD", True):
            register_djule_autoreload()
