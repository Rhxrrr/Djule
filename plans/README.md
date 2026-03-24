# Djule
A modular frontend framework for Django that delivers a SPA-like experience using a client-side router, aggressive caching with version detection, and lazy-loaded sections. Built with plain HTML and TypeScript, it extends Django’s template engine with more expressive Python-driven logic compiled during a build step.

## Plan

### 1. Foundation and Project Shape
- Define the core product boundaries: what Djule owns at build time, what it owns in the browser, and what remains standard Django behavior.
- Establish the package layout for the Django integration, TypeScript runtime, template compiler, and shared manifest/versioning utilities.
- Decide the public API surface for app setup, route registration, section loading, and cache invalidation hooks.
- Create a minimal example app that proves the framework can run inside a normal Django project without requiring a custom backend stack.

### 2. Django Integration Layer
- Build the Django app package, settings helpers, template tags, and management commands needed to install Djule into a project cleanly.
- Define how pages, layouts, and lazy sections are declared so Django templates remain first-class instead of being wrapped by a JavaScript-only abstraction.
- Add a server-rendered bootstrap flow that exposes route metadata, asset versions, and initial page state to the client runtime.
- Keep progressive enhancement as a baseline so navigation still works with full page loads when JavaScript is unavailable or fails.

### 3. Client Runtime and Router
- Implement a client-side router that intercepts same-origin navigation, preserves browser history semantics, and falls back safely when a route should bypass SPA behavior.
- Build the section loader so only the required page fragments and scripts are fetched for a transition.
- Add transition lifecycle hooks for before-load, swap, hydrate, after-load, and error handling so the framework can support plugins later.
- Support scroll restoration, active link state, and predictable re-entry behavior on back/forward navigation.

### 4. Cache and Versioning System
- Design an aggressive cache strategy for templates, sections, route payloads, and static assets.
- Introduce a version manifest that can detect deploy changes and invalidate only the stale pieces rather than clearing everything blindly.
- Add stale-while-revalidate behavior where it improves perceived speed without risking incorrect content.
- Define how the browser cache, Django responses, and the Djule runtime coordinate on version mismatches and forced refreshes.

### 5. Template Compiler
- Design the extended template syntax so it feels Pythonic and expressive while still compiling down to something Django can render safely.
- Build a compile step that transforms the enhanced syntax into standard Django-compatible templates or intermediate artifacts.
- Add source maps or compiler diagnostics that point back to the original template files so errors stay debuggable.
- Validate that the compiler preserves Django template concepts like inheritance, includes, blocks, filters, and context handling.

### 6. Lazy Loading and Fragment Delivery
- Define the fragment protocol for requesting only the parts of a page needed for navigation or partial updates.
- Add server and client conventions for named sections, nested layouts, and dependency ordering.
- Ensure sections can opt into eager, lazy, or conditional loading depending on route and viewport needs.
- Prevent duplicate execution of scripts and duplicate hydration of already-mounted UI behavior during repeated visits.

### 7. Developer Experience
- Add a build command that compiles templates, generates manifests, and validates routes before deployment.
- Add a dev workflow with fast rebuilds, useful compiler errors, and clear reporting when router or section contracts are violated.
- Document the mental model with one simple example, one multi-page example, and one lazy-loaded dashboard-style example.
- Keep configuration minimal, with strong defaults and a small number of explicit extension points.

### 8. Testing and Reliability
- Add unit tests for the compiler, manifest/version logic, and router state transitions.
- Add integration tests for Django responses, fragment requests, cache invalidation, and back/forward navigation behavior.
- Create fixtures that simulate deploy version bumps to verify stale caches recover cleanly.
- Include failure-mode testing for network interruptions, malformed fragments, and server/client version drift.

### 9. Release Readiness
- Package Djule as an installable library with a clear Django setup guide and a stable starter template.
- Publish a versioned roadmap for the first usable milestone, the beta milestone, and the first stable release.
- Define plugin or extension boundaries only after the core router, compiler, and cache lifecycle are proven stable.
- Keep the first release focused on reliability and clarity over feature breadth.

## Near-Term Milestones

### Milestone 1: Vertical Slice
- Django app integration
- Basic client-side router
- One lazy-loaded section flow
- Simple asset/version manifest
- Example project proving end-to-end navigation

### Milestone 2: Compiler and Fragment Model
- Extended template syntax prototype
- Build pipeline that emits Django-compatible output
- Fragment request/response contract
- Route metadata generation
- Better development diagnostics

### Milestone 3: Hardening
- Robust cache invalidation rules
- Navigation lifecycle hooks
- Test coverage across server and client boundaries
- Packaging, docs, and install story
- Release candidate for external testing
