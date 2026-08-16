# Task 7 Report: Convert macOS packaging to an explicit ARM64/x86_64 matrix

## Summary

Converted the single `build-macos` CI job into a 2-entry matrix that produces
native ARM64 and x86_64 macOS binaries. Each matrix variant builds both Full
and Lite, verifies architecture with `verify_macos_bundle.py`, and uploads
architecture-labeled artifacts. The release job now publishes all 6 assets
(2 Windows + 4 macOS).

## Changes

### `.github/workflows/build.yml`
- Replaced `runs-on: macos-latest` with `runs-on: ${{ matrix.runner }}`
- Added `strategy.matrix.include` with two entries:
  - ARM64: `macos-latest`, arch `arm64`, label `ARM64`
  - x86-64: `macos-15-large`, arch `x86_64`, label `x86-64`
- Added "Check Runner Machine Architecture" step that asserts `uname -m`
- Both Full and Lite builds now set `WECHAT_MP_TOOLS_TARGET_ARCH`
- Added verification steps after each build using `verify_macos_bundle.py`
- Zip and artifact names include `arch_label` (ARM64/x86-64)
- Release `files` lists all 6 explicit artifact paths
- Windows job unchanged

### `tests/test_build_workflow.py` (new)
- 27 PyYAML-based tests in 4 test classes:
  - `TestMacOSMatrixStructure`: matrix shape, runner/arch mappings
  - `TestMacOSBuildSteps`: TARGET_ARCH, verify steps, arch labels in zips/artifacts
  - `TestReleaseAssets`: all 4 macOS + 2 Windows variants in release
  - `TestDocumentation`: README/BUILD.md mention x86_64, ARM64, macos-15-large

### `README.md`
- Added macOS version selection table (Apple Silicon → ARM64, Intel → x86-64)
- Guidance on checking chip type via  → About This Mac

### `BUILD.md`
- Updated CI artifacts list to show 4 macOS variants
- Added Intel Mac local build section with `WECHAT_MP_TOOLS_TARGET_ARCH=x86_64`
- Documented `macos-15-large` as CI Intel runner

## Verification

- 27/27 workflow tests pass
- 83/83 total test suite passes
- YAML parses cleanly with PyYAML
- `${{ }}` expressions in shell `run:` blocks are double-quoted
