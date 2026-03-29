from __future__ import annotations

from pathlib import Path
from types import CodeType
from typing import ClassVar, Mapping

from djule.compiler.cache_support import DjuleCacheMixin
from djule.compiler.import_support import DjuleImportMixin
from djule.compiler.plan_support import DjulePlanMixin
from djule.compiler.render_plan import ComponentPlan
from djule.compiler.render_support import DjuleRenderMixin
from djule.compiler.types import ExternalComponent, ImportedComponentRef, RendererError, SafeHtml
from djule.parser.ast_nodes import Module


class DjuleRenderer(DjuleCacheMixin, DjulePlanMixin, DjuleImportMixin, DjuleRenderMixin):
    """Render Djule modules to HTML.

    The renderer is intentionally composed from focused support mixins so the
    main public API stays small and the cache, plan, import, and render
    responsibilities remain easier to reason about.
    """

    CACHE_VERSION: ClassVar[int] = 6
    _parsed_module_cache: ClassVar[dict[Path, tuple[int, int, Module]]] = {}
    _compiled_expr_cache: ClassVar[dict[str, CodeType]] = {}
    _entry_plan_cache: ClassVar[
        dict[tuple[Path, str], tuple[int, int, ComponentPlan, tuple[tuple[str, int, int], ...]]]
    ] = {}

    DEFAULT_BUILTINS: Mapping[str, object] = {
        "bool": bool,
        "dict": dict,
        "enumerate": enumerate,
        "int": int,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "range": range,
        "set": set,
        "str": str,
        "sum": sum,
        "tuple": tuple,
    }

    def __init__(
        self,
        module: Module,
        component_registry: Mapping[str, ExternalComponent] | None = None,
        builtins: Mapping[str, object] | None = None,
        *,
        module_path: Path | None = None,
        search_paths: list[Path] | None = None,
        renderer_cache: dict[Path, "DjuleRenderer"] | None = None,
    ) -> None:
        """Initialize a renderer for one parsed Djule module.

        The renderer keeps per-instance import registries and compiled plans,
        while the mixins provide shared caches for parsed modules, compiled
        expressions, and persisted entry plans.
        """
        self.module = module
        self.module_path = module_path.resolve() if module_path else None
        self.internal_components = {component.name: component for component in module.components}
        self.component_registry = dict(component_registry or {})
        self.builtins = dict(self.DEFAULT_BUILTINS)
        if builtins:
            self.builtins.update(builtins)
        self.search_paths = [path.resolve() for path in (search_paths or [])]
        self.renderer_cache = renderer_cache if renderer_cache is not None else {}
        if self.module_path is not None:
            self.renderer_cache[self.module_path] = self
        self.auto_component_registry: dict[str, ImportedComponentRef] = {}
        self.auto_module_registry: dict[str, "DjuleRenderer"] = {}
        self.imports_loaded = False
        self._instance_component_plans: dict[str, ComponentPlan] = {}
        self._plan_dependency_paths: set[Path] | None = None
        self._current_component_name: str | None = None
