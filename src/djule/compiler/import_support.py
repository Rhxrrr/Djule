from __future__ import annotations

import inspect
from pathlib import Path

from djule.compiler.types import ImportedComponentRef, RendererError, SafeHtml
from djule.parser.ast_nodes import ComponentDef, ImportFrom, ImportModule


class DjuleImportMixin:
    """Helpers for resolving Djule component imports across modules."""
    def _resolve_component(self, name: str):
        """Resolve a component name from local, manual, or imported registries."""
        if name in self.internal_components:
            return self.internal_components[name]

        if name in self.component_registry:
            return self.component_registry[name]

        self._load_auto_imports()
        if name in self.auto_component_registry:
            return self.auto_component_registry[name]

        if "." in name:
            namespace, component_name = name.rsplit(".", 1)
            module_renderer = self.auto_module_registry.get(namespace)
            if module_renderer is not None:
                return ImportedComponentRef(renderer=module_renderer, component_name=component_name)

        return None

    def _load_auto_imports(self) -> None:
        """Populate component and module registries from the module's import nodes.

        This work is delayed until the first import-backed component lookup so
        renderers that never touch imports avoid unnecessary module resolution.
        """
        if self.imports_loaded:
            return

        for import_node in self.module.imports:
            module_renderer = self._load_imported_module(import_node.module)
            if isinstance(import_node, ImportFrom):
                for name in import_node.names:
                    if name not in module_renderer.internal_components:
                        raise RendererError(
                            f"Imported component '{name}' was not found in module '{import_node.module}'"
                        )
                    self.auto_component_registry[name] = ImportedComponentRef(
                        renderer=module_renderer,
                        component_name=name,
                    )
                continue

            namespace = self._module_import_namespace(import_node)
            self.auto_module_registry[namespace] = module_renderer

        self.imports_loaded = True

    @staticmethod
    def _module_import_namespace(import_node: ImportModule) -> str:
        """Return the namespace string exposed by a module import."""
        if import_node.alias:
            return import_node.alias
        if import_node.module.startswith("."):
            raise RendererError(
                f"Relative module import '{import_node.module}' must use 'as <alias>' to create a usable component namespace"
            )
        return import_node.module

    def _load_imported_module(self, module_name: str):
        """Load or reuse a renderer for an imported Djule module."""
        module_path = self._resolve_module_path(module_name)
        cached_renderer = self.renderer_cache.get(module_path)
        if cached_renderer is not None:
            return cached_renderer

        return self.from_file(
            module_path,
            component_registry=self.component_registry,
            builtins=self.builtins,
            search_paths=self.search_paths,
            renderer_cache=self.renderer_cache,
        )

    def _resolve_module_path(self, module_name: str) -> Path:
        """Resolve an absolute or relative Djule import to an on-disk module path."""
        if module_name.startswith("."):
            return self._resolve_relative_module_path(module_name)

        module_parts = module_name.split(".")
        candidates: list[Path] = []

        for base_path in self.search_paths:
            candidates.append(base_path.joinpath(*module_parts).with_suffix(".djule"))
            candidates.append(base_path.joinpath(*module_parts, "__init__.djule"))

        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()

        searched_paths = ", ".join(str(path) for path in candidates) or "<no search paths configured>"
        importer = f" from '{self.module_path}'" if self.module_path is not None else ""
        raise RendererError(f"Could not resolve imported module '{module_name}'{importer}. Searched: {searched_paths}")

    def _resolve_relative_module_path(self, module_name: str) -> Path:
        """Resolve a relative module import from the current module's directory."""
        if self.module_path is None:
            raise RendererError(
                f"Could not resolve relative import '{module_name}' without a source file path"
            )

        leading_dots = len(module_name) - len(module_name.lstrip("."))
        remainder = module_name[leading_dots:]
        module_parts = remainder.split(".") if remainder else []

        base_path = self.module_path.parent
        for _ in range(max(leading_dots - 1, 0)):
            base_path = base_path.parent

        candidates = []
        if module_parts:
            candidates.append(base_path.joinpath(*module_parts).with_suffix(".djule"))
            candidates.append(base_path.joinpath(*module_parts, "__init__.djule"))
        else:
            candidates.append(base_path / "__init__.djule")

        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()

        searched_paths = ", ".join(str(path) for path in candidates)
        importer = f" from '{self.module_path}'" if self.module_path is not None else ""
        raise RendererError(f"Could not resolve imported module '{module_name}'{importer}. Searched: {searched_paths}")

    def _render_resolved_component(
        self,
        component_name: str,
        component,
        props: dict[str, object],
    ) -> SafeHtml:
        """Render a component once it has already been resolved to a callable target."""
        self._validate_component_props(component_name, component, props)

        if isinstance(component, ComponentDef):
            return self._render_component_def(component, props)

        if isinstance(component, ImportedComponentRef):
            return component.renderer._render_component_by_name(component.component_name, props)

        result = component(**props)
        if isinstance(result, SafeHtml):
            return result
        return SafeHtml(str(result))

    def _validate_component_props(
        self,
        component_name: str,
        component,
        props: dict[str, object],
    ) -> None:
        """Reject nested children for components that do not declare a `children` prop."""
        if "children" in props and not self._component_accepts_children(component):
            raise RendererError(
                f"Component '{component_name}' received nested content, but it does not declare a 'children' prop"
            )

    @staticmethod
    def _component_accepts_children(component) -> bool:
        """Return whether a component target can legally receive nested content.

        Imported Djule components are resolved recursively. Plain Python callables
        are inspected by signature, with `**kwargs` treated as accepting children.
        """
        if isinstance(component, ImportedComponentRef):
            resolved = component.renderer._resolve_component(component.component_name)
            if resolved is None:
                return False
            return component.renderer._component_accepts_children(resolved)

        if isinstance(component, ComponentDef):
            return "children" in component.params

        try:
            signature = inspect.signature(component)
        except (TypeError, ValueError):
            return False

        for parameter in signature.parameters.values():
            if parameter.kind == inspect.Parameter.VAR_KEYWORD:
                return True

        return "children" in signature.parameters
