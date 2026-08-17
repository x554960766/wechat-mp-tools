# Repository architecture learning kit and macOS x86_64 support implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`[ ]`) syntax for tracking.

**Goal:** Create a reusable `ljt-` architecture-analysis skill, produce programmer-oriented architecture/flow documentation, and add verified native macOS x86_64 application packaging.

**Architecture:** The application remains a local `pywebview`/Flask/SPA monolith. The new skill documents and inventories that architecture without changing runtime behavior. macOS distribution changes from one implicit ARM64 job to an explicit ARM64/x86_64 matrix, with each architecture built natively and verified before upload.

**Tech Stack:** Python 3.12, Flask, PyInstaller, PyYAML, pytest, Mermaid, GitHub Actions macOS ARM64 and Intel runners.

## Global constraints

- Do not refactor the Flask, frontend, or platform-client architecture while adding documentation and packaging support.
- Preserve Windows Full/Lite behavior.
- Preserve macOS Full/Lite browser-bundling behavior.
- Treat `backend/subtitle_remover/`, `injection_scripts/lib/`, and `graphify-out/` as vendored/generated material, not first-party teaching material.
- Do not commit credentials, local data, Graphify output, Playwright downloads, or build output.
- Use `x86_64` for Python/GitHub architecture identifiers and `x86-64` only in artifact names where hyphens are clearer.
- Every new helper must have tests written before implementation.
- Every diagram source must have a successfully rendered SVG counterpart.

---

### Task 1: Establish structural tests for the ljt skill

**Files:**

- Create: `tests/test_ljt_skill.py`
- Create: `skills/ljt-repo-architect/SKILL.md`
- Create: `skills/ljt-repo-architect/references/repository-map.md`
- Create: `skills/ljt-repo-architect/references/architecture-guide.md`
- Create: `skills/ljt-repo-architect/references/diagram-patterns.md`
- Create: `skills/ljt-repo-architect/references/quality-checklist.md`

**Interfaces:**

- Produces: Agent Skills package named `ljt-repo-architect`.
- Produces: reference files referenced only one level below `SKILL.md`.

- [ ] **Step 1: Write the failing structural test**

```python
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "ljt-repo-architect"
SKILL_FILE = SKILL_DIR / "SKILL.md"


def parse_frontmatter():
    text = SKILL_FILE.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    end = text.index("\n---\n", 4)
    return yaml.safe_load(text[4:end])


def test_skill_name_matches_directory_and_description_is_actionable():
    metadata = parse_frontmatter()
    assert metadata["name"] == "ljt-repo-architect"
    description = metadata["description"].lower()
    assert "repository" in description
    assert "architecture" in description
    assert "when" in description or "use" in description


def test_progressive_disclosure_files_exist_and_skill_is_compact():
    for relative in (
        "references/repository-map.md",
        "references/architecture-guide.md",
        "references/diagram-patterns.md",
        "references/quality-checklist.md",
    ):
        assert (SKILL_DIR / relative).is_file(), relative
    assert len(SKILL_FILE.read_text(encoding="utf-8").splitlines()) <= 500
```

- [ ] **Step 2: Verify the test fails**

Run:

```bash
python3 -m pytest tests/test_ljt_skill.py -q
```

Expected result: failure because `skills/ljt-repo-architect/SKILL.md` does not exist.

- [ ] **Step 3: Create the skill and focused references**

Write a concise `SKILL.md` workflow with:

1. inventory the Git-tracked repository
2. separate first-party code from vendored/generated code
3. trace entry points and runtime behavior
4. map modules, interfaces, storage, threads, and external systems
5. create architecture/flow diagrams
6. create a staged programmer learning guide
7. verify evidence and diagram rendering

Put detailed rules and examples in the four reference files.

- [ ] **Step 4: Verify the skill test passes**

Run:

```bash
python3 -m pytest tests/test_ljt_skill.py -q
```

Expected result: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/test_ljt_skill.py skills/ljt-repo-architect
git commit -m "feat: add ljt repository architecture skill"
```

### Task 2: Add a tested repository inventory helper to the skill

**Files:**

- Create: `tests/test_skill_inventory.py`
- Create: `skills/ljt-repo-architect/scripts/inventory.py`

**Interfaces:**

- Produces: `classify_path(path: PurePosixPath) -> str | None`
- Produces CLI behavior: `python skills/ljt-repo-architect/scripts/inventory.py --root . --json`
- Output groups: `entrypoints`, `backend`, `frontend`, `injection`, `docs`, `build`, `vendored`, and `other`.

- [ ] **Step 1: Write failing unit and CLI tests**

Load the script explicitly with `importlib.util.spec_from_file_location` so the helper remains a single self-contained skill script:

```python
import importlib.util
import json
import subprocess
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/ljt-repo-architect/scripts/inventory.py"
spec = importlib.util.spec_from_file_location("ljt_inventory", SCRIPT)
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)
```

The test must assert:

```python
assert classify_path(PurePosixPath("main.py")) == "entrypoints"
assert classify_path(PurePosixPath("backend/douyin.py")) == "backend"
assert classify_path(PurePosixPath("frontend/js/api.js")) == "frontend"
assert classify_path(PurePosixPath("injection_scripts/src/feed.js")) == "injection"
assert classify_path(PurePosixPath("backend/subtitle_remover/x.py")) == "vendored"
assert classify_path(PurePosixPath("injection_scripts/lib/x.js")) == "vendored"
assert classify_path(PurePosixPath("graphify-out/graph.json")) == "vendored"
```

Run the CLI in the repository root and assert:

- exit code is 0
- output is one JSON object
- `root` is absolute
- `tracked_files` is greater than 100
- `groups.backend` contains `backend/douyin.py`
- no group contains `graphify-out/...`

- [ ] **Step 2: Verify tests fail**

```bash
python3 -m pytest tests/test_skill_inventory.py -q
```

Expected result: failure because `inventory.py` does not exist.

- [ ] **Step 3: Implement the helper**

Use only the Python standard library:

- enumerate files with `git ls-files --cached --others --exclude-standard`
- normalize separators to POSIX
- classify only Git-visible files
- emit deterministic, sorted JSON
- support `--json`

- [ ] **Step 4: Verify tests pass**

```bash
python3 -m pytest tests/test_skill_inventory.py -q
```

Expected result: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_skill_inventory.py skills/ljt-repo-architect/scripts/inventory.py
git commit -m "feat: add repository inventory helper"
```

### Task 3: Create and render the architecture diagrams

**Files:**

- Create: `tests/test_architecture_diagrams.py`
- Create: `docs/diagrams/software-architecture.mmd`
- Create: `docs/diagrams/application-flow.mmd`
- Create: `docs/diagrams/build-flow.mmd`
- Create: rendered SVG files matching each source.

**Interfaces:**

- Produces editable Mermaid sources for GitHub rendering.
- Produces non-empty SVG artifacts for offline use.

- [ ] **Step 1: Write failing diagram tests**

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIAGRAMS = ROOT / "docs" / "diagrams"


def test_required_diagrams_have_sources_and_rendered_svgs():
    for name in ("software-architecture", "application-flow", "build-flow"):
        source = DIAGRAMS / f"{name}.mmd"
        svg = DIAGRAMS / f"{name}.svg"
        assert source.is_file()
        assert source.stat().st_size > 500
        assert svg.is_file()
        assert svg.stat().st_size > 1000
```

- [ ] **Step 2: Verify tests fail**

```bash
python3 -m pytest tests/test_architecture_diagrams.py -q
```

Expected result: failure because the diagram directory is absent.

- [ ] **Step 3: Write diagram sources**

`software-architecture.mmd` must show:

- desktop/browser entries
- frontend SPA layers
- Flask blueprints
- shared domain infrastructure
- platform clients and external sites
- Playwright/mitmproxy/injection scripts
- storage and media tools
- packaging pipeline

`application-flow.mmd` must show:

- startup
- routing
- API request
- login/account acquisition when required
- platform parsing
- background download
- progress polling
- file/history persistence
- cancellation/error paths

`build-flow.mmd` must show:

- Windows Full/Lite path
- macOS ARM64 Full/Lite path
- macOS x86_64 Full/Lite path
- architecture verification
- artifact and release publication

- [ ] **Step 4: Render every source**

```bash
python3 /Users/santianpin/.agents/skills/diagram-generator/scripts/render_diagram.py \
  docs/diagrams/software-architecture.mmd \
  --format svg --out docs/diagrams/software-architecture.svg
```

Repeat for `application-flow` and `build-flow`.

- [ ] **Step 5: Verify diagram tests pass**

```bash
python3 -m pytest tests/test_architecture_diagrams.py -q
```

- [ ] **Step 6: Commit**

```bash
git add tests/test_architecture_diagrams.py docs/diagrams
git commit -m "docs: add architecture and runtime diagrams"
```

### Task 4: Write the architecture guide and programmer learning path

**Files:**

- Create: `tests/test_architecture_docs.py`
- Create: `docs/ARCHITECTURE.md`
- Create: `docs/CODE_LEARNING_PATH.md`

**Interfaces:**

- Produces architecture documentation consumed by maintainers.
- Produces a staged learning path with concrete files, commands, and trace scenarios.

- [ ] **Step 1: Write failing documentation tests**

The tests must require:

- both documents exist
- each links all three diagram source files
- `ARCHITECTURE.md` explains `main.py`, `app.py`, blueprints, `backend/config.py`, `backend/runtime.py`, account pool, RSS scheduler, mitmproxy, transcode, frontend router/API, injection scripts, PyInstaller, and data directories
- `CODE_LEARNING_PATH.md` contains at least five staged exercises and exact commands
- neither document contains unfinished-work markers

- [ ] **Step 2: Verify tests fail**

```bash
python3 -m pytest tests/test_architecture_docs.py -q
```

- [ ] **Step 3: Write evidence-based documentation**

Use source paths and observed module responsibilities. Do not copy Graphify community labels blindly. Include a “where to start for a bug” routing table.

- [ ] **Step 4: Verify tests pass**

```bash
python3 -m pytest tests/test_architecture_docs.py -q
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_architecture_docs.py docs/ARCHITECTURE.md docs/CODE_LEARNING_PATH.md
git commit -m "docs: add repository architecture learning guide"
```

### Task 5: Add tested PyInstaller target-architecture selection

**Files:**

- Create: `tests/test_build_architecture.py`
- Modify: `wechat_mp_tools.spec`

**Interfaces:**

- Produces spec behavior controlled by `WECHAT_MP_TOOLS_TARGET_ARCH`.
- Allowed macOS values: `arm64`, `x86_64`.
- Non-macOS and absent values resolve to `None`.
- Unsupported values must raise `ValueError`.

- [ ] **Step 1: Write failing tests**

Tests should parse the spec as text and assert:

- it defines a target-architecture resolver
- macOS `EXE` uses that resolver instead of literal `target_arch=None`
- Windows/Linux remain `target_arch=None`

Use `runpy` only if a safe spec harness is added; do not import the spec directly because `PyInstaller` may not be installed in every test environment.

- [ ] **Step 2: Verify tests fail**

```bash
python3 -m pytest tests/test_build_architecture.py -q
```

Expected result: failure because the current macOS `EXE` always uses `target_arch=None`.

- [ ] **Step 3: Implement target selection**

Add a small resolver near the top of the spec:

```python
def macos_target_arch(value=None):
    if sys.platform != "darwin":
        return None
    requested = (value or os.environ.get("WECHAT_MP_TOOLS_TARGET_ARCH", "")).strip().lower()
    if not requested:
        return None
    if requested not in {"arm64", "x86_64"}:
        raise ValueError("WECHAT_MP_TOOLS_TARGET_ARCH must be arm64 or x86_64 on macOS")
    return requested
```

Use `target_arch=macos_target_arch()` in the macOS `EXE` only.

- [ ] **Step 4: Verify tests pass**

```bash
python3 -m pytest tests/test_build_architecture.py -q
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_build_architecture.py wechat_mp_tools.spec
git commit -m "build: select explicit macOS target architecture"
```

### Task 6: Add a tested macOS bundle architecture verifier

**Files:**

- Create: `tests/test_verify_macos_bundle.py`
- Create: `scripts/verify_macos_bundle.py`

**Interfaces:**

- Produces `architectures(path: Path) -> set[str]`
- Produces CLI: `python scripts/verify_macos_bundle.py <WeChat MP Tools.app> <arm64|x86_64>`
- Exits 0 only when the main executable, at least one native extension, and any bundled Chromium executable support the requested architecture.

- [ ] **Step 1: Write failing tests**

Cover:

- parsing `lipo -archs` output
- requiring the main executable
- requiring a `.dylib`/`.so` architecture match
- checking Chromium when bundled
- succeeding for a universal binary that includes the requested architecture
- rejecting an unsupported requested architecture before subprocess execution

- [ ] **Step 2: Verify tests fail**

```bash
python3 -m pytest tests/test_verify_macos_bundle.py -q
```

- [ ] **Step 3: Implement the verifier**

Use standard-library modules plus external `file`/`lipo` commands available on macOS runners. Keep process execution injectable for tests.

- [ ] **Step 4: Verify tests pass**

```bash
python3 -m pytest tests/test_verify_macos_bundle.py -q
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_verify_macos_bundle.py scripts/verify_macos_bundle.py
git commit -m "build: verify macOS bundle architectures"
```

### Task 7: Convert macOS packaging to an explicit ARM64/x86_64 matrix

**Files:**

- Create or extend: `tests/test_build_workflow.py`
- Modify: `.github/workflows/build.yml`
- Modify: `README.md`
- Modify: `BUILD.md`

**Interfaces:**

- Produces four macOS artifacts:
  - ARM64 Full
  - ARM64 Lite
  - x86-64 Full
  - x86-64 Lite
- Consumes `scripts/verify_macos_bundle.py`.

- [ ] **Step 1: Write failing workflow tests**

Parse `.github/workflows/build.yml` with PyYAML and assert:

- macOS job has a two-entry matrix
- ARM64 uses `macos-latest`
- x86_64 uses `macos-15-intel`
- both Full and Lite build commands set `WECHAT_MP_TOOLS_TARGET_ARCH`
- every build invokes the verifier
- artifact names contain `ARM64` or `x86-64`
- release file globs or explicit paths include all four variants
- README and BUILD docs mention x86_64 and `macos-15-intel`

- [ ] **Step 2: Verify tests fail**

```bash
python3 -m pytest tests/test_build_workflow.py -q
```

- [ ] **Step 3: Implement the matrix**

Keep Windows unchanged. In each macOS matrix variant:

1. check runner machine architecture
2. install dependencies and Chromium
3. build Full
4. verify architecture
5. zip with architecture label
6. clean build output
7. build Lite
8. verify architecture
9. zip with architecture label
10. upload both artifacts

- [ ] **Step 4: Update user documentation**

Document:

- Intel Mac users should download `x86-64`
- Apple Silicon users should download `ARM64`
- how to build x86_64 locally on an Intel Mac
- why CI uses a native Intel runner

- [ ] **Step 5: Verify workflow tests pass**

```bash
python3 -m pytest tests/test_build_workflow.py -q
```

- [ ] **Step 6: Commit**

```bash
git add tests/test_build_workflow.py .github/workflows/build.yml README.md BUILD.md
git commit -m "build: publish native macOS x86_64 apps"
```

### Task 8: Final verification and cleanup

**Files:**

- Modify: `.gitignore`
- Modify: generated documentation as needed.

- [ ] **Step 1: Exclude local Graphify output**

Add:

```gitignore
# Local architecture-analysis graph
graphify-out/
```

- [ ] **Step 2: Run the complete local suite**

```bash
python3 -m pytest tests -q
```

- [ ] **Step 3: Run syntax checks**

```bash
python3 -m py_compile skills/ljt-repo-architect/scripts/inventory.py scripts/verify_macos_bundle.py
```

- [ ] **Step 4: Run workflow/spec/doc-specific tests**

```bash
python3 -m pytest \
  tests/test_ljt_skill.py \
  tests/test_skill_inventory.py \
  tests/test_architecture_diagrams.py \
  tests/test_architecture_docs.py \
  tests/test_build_architecture.py \
  tests/test_verify_macos_bundle.py \
  tests/test_build_workflow.py -q
```

- [ ] **Step 5: Inspect Git state**

```bash
git status --short
git diff --check
git log --oneline --decorate -8
```

Expected result: no unintended generated files and all intended changes committed.
