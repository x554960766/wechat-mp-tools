# Quality Checklist — Verification

## Purpose

Complete this checklist during Phase 6 (Verification) before delivering
the final architecture document.

## Inventory Verification

- [ ] Tracked file count from `git ls-files | wc -l` matches inventory total.
- [ ] Every file in the inventory is assigned exactly one category.
- [ ] Vendored/generated directories are explicitly listed.

## Entry-Point Verification

- [ ] Each declared entry point has a file path that exists.
- [ ] At least one call chain of depth >= 2 is traced for every entry point.
- [ ] Async boundaries are annotated.

## Module Verification

- [ ] Every first-party source file belongs to exactly one module.
- [ ] Module interfaces list only symbols that are actually exported.
- [ ] Cross-module dependency graph has no unexplained edges.

## Diagram Verification

- [ ] All Mermaid blocks render without syntax errors.
- [ ] Every module/node in diagrams corresponds to a real file or module.
- [ ] Sequence diagrams cover the top 2–3 runtime flows.
- [ ] No orphaned diagram elements (every node is reachable).

## Learning Guide Verification

- [ ] Stage 1 requires no prior knowledge of the codebase.
- [ ] Each stage references specific file paths.
- [ ] Progression from stage N to N+1 builds on previous content.

## Final Checks

- [ ] Document is a single self-contained Markdown file.
- [ ] No absolute paths outside the repository are referenced.
- [ ] All external services are named and their role explained.
