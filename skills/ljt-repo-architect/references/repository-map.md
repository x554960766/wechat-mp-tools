# Repository Map — Classification Taxonomy

## Purpose

This reference defines the categories used in Phase 1 (Inventory) and
provides the template for recording results.

## File Categories

| Category | Definition | Examples |
|----------|-----------|----------|
| **first-party source** | Code written by project authors | `src/`, `lib/`, `app/` |
| **vendored** | Third-party code committed verbatim | `vendor/`, `third_party/` |
| **generated** | Output of build tools or code generators | `dist/`, `*.generated.*`, `swagger.json` |
| **config/CI** | Build, lint, CI/CD, and environment config | `.github/`, `Makefile`, `Dockerfile` |
| **docs/tests** | Documentation and test suites | `docs/`, `tests/`, `README.md` |
| **data/assets** | Static assets, seed data, migrations | `migrations/`, `public/`, `seeds/` |

## Classification Rules

1. A file can belong to only one category. Use the **primary purpose** rule:
   ask "why does this file exist?" and pick the closest match.
2. Symlinks are classified by their target.
3. Empty directories are ignored unless they are meaningful (e.g. `__pycache__`
   is always generated).
4. Lock files (`package-lock.json`, `Poetry.lock`) are classified as config.

## Inventory Template

```markdown
## Inventory

| Category | Count | Key Paths |
|----------|-------|-----------|
| first-party source | ? | ... |
| vendored | ? | ... |
| generated | ? | ... |
| config/CI | ? | ... |
| docs/tests | ? | ... |
| data/assets | ? | ... |
| **Total tracked files** | ? | |
```

## Tips

- Use `git ls-files | wc -l` for the total.
- Use `file` or extension heuristics for ambiguous cases.
- If a directory mixes categories (e.g. `src/` contains `.proto` generated
  files), classify individual files, not the directory.
