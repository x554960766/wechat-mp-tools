---
name: ljt-repo-architect
description: >
  Produce a repository architecture analysis and staged learning guide.
  Use when you need to understand an unfamiliar codebase's structure,
  entry points, module boundaries, and runtime behavior before making
  changes. Works on any Git-tracked repository.
---

# ljt-repo-architect

Analyse a repository's architecture and generate a programmer learning guide
in staged detail.

## Workflow

Follow these phases **in order**. Each phase gates the next; do not skip ahead.

### Phase 1 — Inventory

1. `git ls-files` to list every tracked path.
2. Classify each path into: **first-party source**, **vendored**, **generated**,
   **config/CI**, **docs/tests**, **data/assets**.
3. Record the classification in a summary table.

See [references/repository-map.md](references/repository-map.md) for the full
classification taxonomy and inventory template.

### Phase 2 — Entry-point and runtime tracing

1. Identify CLI entry points, HTTP listeners, message handlers, cron jobs,
   and any other runtime triggers.
2. Trace the call chain from each entry point through at least three layers.
3. Note async boundaries, thread models, and process isolation.

See [references/architecture-guide.md](references/architecture-guide.md) for
tracing procedures and patterns.

### Phase 3 — Module and interface mapping

1. Group source files into coherent modules by responsibility.
2. Document each module's public interface (exports, API routes, class
   contracts).
3. Map cross-module dependencies; identify coupling hotspots.
4. Catalog storage backends, caches, and external system integrations.

### Phase 4 — Diagrams

1. Create a **system-context diagram** showing the repo's place among
   external services.
2. Create a **component diagram** showing internal modules and their
   dependencies.
3. Create **sequence diagrams** for the 2–3 most important runtime flows.
4. Use only Mermaid; all diagrams must render in GitHub-flavored Markdown.

See [references/diagram-patterns.md](references/diagram-patterns.md) for
standard diagram templates and conventions.

### Phase 5 — Staged learning guide

Produce a progressive reading order:

| Stage | Audience | Content |
|-------|----------|---------|
| 1 | New contributor | Directory map, quick-start, entry points |
| 2 | Developer adding features | Module internals, interfaces, data flow |
| 3 | Developer debugging/operating | Runtime tracing, external deps, failure modes |
| 4 | Architect | Coupling analysis, tech-debt inventory, extension points |

### Phase 6 — Verification

1. Confirm every file referenced in diagrams exists in the inventory.
2. Confirm every external service listed is reachable or documented.
3. Run `git ls-files | grep -c ''` and compare against inventory totals.
4. Validate all Mermaid diagrams render without errors.

See [references/quality-checklist.md](references/quality-checklist.md) for the
complete verification checklist.

## Output

Deliver a single Markdown document containing:
- Inventory summary table
- Architecture narrative with inline diagrams
- Staged learning guide
- Verification evidence appendix
