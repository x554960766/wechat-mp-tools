# Architecture Guide — Tracing Procedures

## Purpose

This reference describes how to identify entry points, trace runtime
behavior, and document module interfaces (Phase 2 & 3).

## Entry-Point Discovery

### Common Entry-Point Patterns

| Pattern | Signal | How to Find |
|---------|--------|-------------|
| CLI binary | `if __name__ == "__main__"`, `package.json` `bin` field, `main()` in Go/Rust | `grep -R "__main__"`, inspect `setup.py`/`Cargo.toml` |
| HTTP listener | `app.listen`, `router.add_route`, `@app.route` | Search for port binding, route decorators |
| Message handler | `on_message`, `subscribe`, handler registration | Search for event/callback registration |
| Cron/scheduled | `cron`, `schedule`, `@periodic_task` | Search for time-based triggers |
| Build hook | `pre-commit`, `lifecycle` hooks in `pom.xml` | Insure config files |

## Tracing Procedure

1. **Start at the entry point.** Mark it as depth 0.
2. **Follow synchronous calls** into the first callee. Mark as depth 1.
3. Continue until you hit one of these stop conditions:
   - An external service call (HTTP, DB, message queue).
   - An async boundary (`await`, `go func`, `Thread.start`).
   - A callback or event dispatch.
   - Depth exceeds 3 (document the remaining as "delegates to").
4. Record the chain as: `entry -> d1 -> d2 -> d3 -> [stop reason]`.

## Module Mapping

### Grouping Heuristics

- Files that import each other frequently belong to the same module.
- A directory with its own `__init__.py`, `package.json`, or `mod.rs`
  is usually a module boundary.
- Shared utilities used by > 3 other modules are a cross-cutting concern;
  document them separately.

### Interface Documentation Template

```markdown
### `<module-name>`
- **Responsibility:** one sentence.
- **Public interface:** exported functions/classes/routes.
- **Depends on:** list of other modules or external systems.
- **Depended on by:** list of consuming modules.
```

## Cross-Module Dependency Rules

1. Draw a directed edge from module A to module B if A imports from B.
2. Flag cycles of length ≤ 4 as coupling hotspots.
3. Modules with in-degree > 5 are "hub" modules — scrutinize for
   single-responsibility violations.
