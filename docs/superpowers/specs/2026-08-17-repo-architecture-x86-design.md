# Repository architecture learning kit and macOS x86_64 support design

## Decision

Build the repository analysis capability as an Agent Skills-compatible skill named `ljt-repo-architect`, and produce architecture/flow documentation from it. Add macOS x86_64 support by building separate ARM64 and x86_64 applications natively on matching GitHub Actions runners.

The selected x86 approach is **native x86_64 runner plus separately labeled installers**. A Universal 2 package is intentionally out of scope for this change because combining two PyInstaller bundles and their Chromium resources adds substantial distribution risk without being required for Intel Mac installation.

## Inputs and constraints

- The active checkout is branch `spt/x86`.
- The skill directory and frontmatter `name` must both be `ljt-repo-architect`, following the Agent Skills specification.
- `SKILL.md` must stay under 500 lines and put detailed guidance in `references/`.
- The generated learning material must distinguish first-party business code from vendored code:
  - first-party: root entry points, `backend/`, `frontend/`, and `injection_scripts/src/`
  - vendored/generated: `backend/subtitle_remover/`, `injection_scripts/lib/`, `graphify-out/`, build outputs
- Architecture and flow diagrams must be committed as editable Mermaid plus rendered SVG.
- The existing macOS workflow uses `macos-latest`, which is an ARM64 image. It cannot produce a reliable x86_64 full bundle because native wheels and bundled Chromium follow the runner architecture.
- GitHub's current macOS Intel labels include `macos-15-large`; the workflow must keep the mapping explicit in a matrix instead of relying on a generic `macos-latest` alias.
- Existing Full/Lite browser bundling behavior must remain unchanged.
- Windows builds must remain unchanged.

## Skill design

Create `skills/ljt-repo-architect/` with:

- `SKILL.md`: concise trigger and workflow, under 500 lines.
- `references/repository-map.md`: how to inventory first-party and vendored code.
- `references/architecture-guide.md`: required architecture questions and evidence rules.
- `references/diagram-patterns.md`: approved Mermaid patterns for architecture, runtime flow, download flow, and build flow.
- `references/quality-checklist.md`: completion gates for evidence, diagrams, and learning material.
- `scripts/inventory.py`: dependency-free inventory helper that reads Git-tracked files and emits JSON grouped by architectural role.

The skill must require source evidence for every major claim. It must not infer behavior solely from filenames.

## Documentation deliverables

Create:

- `docs/ARCHITECTURE.md`: layered architecture, module map, request/runtime/download/build flows, storage model, and extension recipe.
- `docs/CODE_LEARNING_PATH.md`: a staged path that lets a programmer run, trace, and safely modify the codebase.
- `docs/diagrams/software-architecture.mmd` and `.svg`.
- `docs/diagrams/application-flow.mmd` and `.svg`.
- `docs/diagrams/build-flow.mmd` and `.svg`.

Diagrams must cover:

- desktop and browser startup
- SPA routing/API calls
- Flask blueprints and domain clients
- account pool and scheduler background work
- Playwright/mitmproxy/injection automation
- downloads, history, settings, and media storage
- PyInstaller and GitHub Actions ARM64/x86_64 packaging

## x86_64 build design

Replace the single macOS job with a matrix containing:

- ARM64: `macos-latest`, expected PyInstaller target `arm64`
- x86_64: `macos-15-large`, expected PyInstaller target `x86_64`

Set `WECHAT_MP_TOOLS_TARGET_ARCH` for every macOS build. Update `wechat_mp_tools.spec` so macOS `EXE(... target_arch=...)` consumes that environment value and rejects unsupported values. Leave non-macOS target selection as `None`.

Each matrix variant builds both Full and Lite variants and uploads architecture-labeled artifacts:

- `WeChat-MP-Tools-macOS-ARM64-Full`
- `WeChat-MP-Tools-macOS-ARM64-Lite`
- `WeChat-MP-Tools-macOS-x86-64-Full`
- `WeChat-MP-Tools-macOS-x86-64-Lite`

The release job must include all four zipped assets.

Add a CI architecture gate after every macOS build. It must verify:

- runner `platform.machine()` matches the requested architecture
- the application executable contains the requested Mach-O architecture
- at least one native Python extension also contains that architecture
- a bundled Chromium executable, when present, also contains that architecture

## Testing and acceptance

The implementation is complete only when all of the following are true:

1. The skill package passes structural validation required by the Agent Skills naming and frontmatter rules.
2. The inventory script reports first-party and vendored groups without treating generated Graphify output as source.
3. All Mermaid diagram sources render successfully to non-empty SVG files.
4. The architecture and learning documents identify concrete entry points, modules, data stores, background threads/processes, and build paths.
5. Automated tests cover skill structure, inventory grouping, build matrix, PyInstaller target selection, and macOS bundle architecture verification behavior.
6. The workflow contains explicit ARM64 and x86_64 runner mappings and architecture-labeled release assets.
7. `README.md` and `BUILD.md` tell Intel Mac users which artifact to download and how to build locally.
8. The complete relevant test suite passes from the current worktree.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| GitHub changes its Intel macOS label | Keep the architecture mapping explicit and documented; update one matrix entry when GitHub retires `macos-15-large`. |
| A dependency installs an ARM wheel on the Intel runner | Install dependencies and Chromium natively on x86_64, then fail CI if executable/native extension architectures do not match. |
| Full bundle includes wrong Chromium architecture | Verify bundled Chromium during the Full build; Lite builds omit it by design. |
| Generated Graphify output obscures the real architecture | Exclude it from version control and separate first-party code from vendored code in the skill and docs. |
| Generic artifact names mislead Intel users | Use explicit ARM64 and x86-64 artifact and zip names. |
