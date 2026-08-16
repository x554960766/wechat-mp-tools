"""Tests that parse .github/workflows/build.yml and verify macOS matrix.

These tests enforce the ARM64/x86_64 dual-architecture CI matrix
by loading the workflow YAML and asserting structural properties.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOW_PATH = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "build.yml"
README_PATH = Path(__file__).resolve().parent.parent / "README.md"
BUILD_PATH = Path(__file__).resolve().parent.parent / "BUILD.md"


@pytest.fixture(scope="session")
def workflow() -> dict:
    with open(WORKFLOW_PATH) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="session")
def readme() -> str:
    return README_PATH.read_text()


@pytest.fixture(scope="session")
def build_md() -> str:
    return BUILD_PATH.read_text()


@pytest.fixture(scope="session")
def macos_job(workflow: dict) -> dict:
    return workflow["jobs"]["build-macos"]


@pytest.fixture(scope="session")
def macos_matrix(macos_job: dict) -> list[dict]:
    return macos_job["strategy"]["matrix"]["include"]


@pytest.fixture(scope="session")
def release_job(workflow: dict) -> dict:
    return workflow["jobs"]["create-release"]


class TestMacOSMatrixStructure:
    """Verify the macOS job uses a 2-entry matrix with correct arch config."""

    def test_matrix_has_two_entries(self, macos_matrix: list[dict]):
        assert len(macos_matrix) == 2, (
            f"Expected 2 matrix entries (ARM64 + x86_64), got {len(macos_matrix)}"
        )

    def test_arm64_entry(self, macos_matrix: list[dict]):
        arm = [e for e in macos_matrix if e.get("arch_label") == "ARM64"]
        assert len(arm) == 1, "Missing ARM64 matrix entry"
        entry = arm[0]
        assert entry["runner"] == "macos-latest"
        assert entry["pyinstaller_arch"] == "arm64"
        assert entry["platform_machine"] == "arm64"

    def test_x86_64_entry(self, macos_matrix: list[dict]):
        x86 = [e for e in macos_matrix if e.get("arch_label") == "x86-64"]
        assert len(x86) == 1, "Missing x86-64 matrix entry"
        entry = x86[0]
        assert entry["runner"] == "macos-15-large"
        assert entry["pyinstaller_arch"] == "x86_64"
        assert entry["platform_machine"] == "x86_64"

    def test_runs_on_uses_matrix_runner(self, macos_job: dict):
        assert macos_job["runs-on"] == "${{ matrix.runner }}"


class TestMacOSBuildSteps:
    """Verify each build step sets TARGET_ARCH and invokes the verifier."""

    def _step_names(self, macos_job: dict) -> list[str]:
        return [s["name"] for s in macos_job["steps"]]

    def _step_by_name(self, macos_job: dict, name: str) -> dict:
        for s in macos_job["steps"]:
            if s["name"] == name:
                return s
        raise AssertionError(f"Step '{name}' not found")

    def _step_run(self, macos_job: dict, name: str) -> str:
        return self._step_by_name(macos_job, name)["run"]

    def test_check_runner_machine_step_exists(self, macos_job: dict):
        names = self._step_names(macos_job)
        assert any("machine" in n.lower() or "arch" in n.lower() for n in names), (
            "Missing a step to verify runner machine architecture"
        )

    def test_full_build_sets_target_arch(self, macos_job: dict):
        run = self._step_run(macos_job, "Build macOS APP (Full)")
        assert "WECHAT_MP_TOOLS_TARGET_ARCH" in run, (
            "Full build must set WECHAT_MP_TOOLS_TARGET_ARCH"
        )
        assert "${{ matrix.pyinstaller_arch }}" in run, (
            "Full build must use matrix.pyinstaller_arch for TARGET_ARCH"
        )

    def test_lite_build_sets_target_arch(self, macos_job: dict):
        run = self._step_run(macos_job, "Build macOS APP (Lite)")
        assert "WECHAT_MP_TOOLS_TARGET_ARCH" in run, (
            "Lite build must set WECHAT_MP_TOOLS_TARGET_ARCH"
        )
        assert "${{ matrix.pyinstaller_arch }}" in run, (
            "Lite build must use matrix.pyinstaller_arch for TARGET_ARCH"
        )

    def test_full_verify_step_exists(self, macos_job: dict):
        names = self._step_names(macos_job)
        assert any("verify" in n.lower() and "full" in n.lower() for n in names), (
            "Missing verification step for Full build"
        )

    def test_lite_verify_step_exists(self, macos_job: dict):
        names = self._step_names(macos_job)
        assert any("verify" in n.lower() and "lite" in n.lower() for n in names), (
            "Missing verification step for Lite build"
        )

    def test_verify_uses_matrix_platform_machine(self, macos_job: dict):
        """Verify steps should check against the correct architecture."""
        for step in macos_job["steps"]:
            if "verify" in step.get("name", "").lower():
                run = step.get("run", "")
                assert "${{ matrix.platform_machine }}" in run, (
                    f"Verify step '{step['name']}' must use matrix.platform_machine"
                )

    def test_full_zip_has_arch_label(self, macos_job: dict):
        run = self._step_run(macos_job, "Compress macOS Full Build")
        assert "${{ matrix.arch_label }}" in run, (
            "Full zip name must include matrix.arch_label"
        )

    def test_lite_zip_has_arch_label(self, macos_job: dict):
        run = self._step_run(macos_job, "Compress macOS Lite Build")
        assert "${{ matrix.arch_label }}" in run, (
            "Lite zip name must include matrix.arch_label"
        )

    def test_full_upload_has_arch_label(self, macos_job: dict):
        for step in macos_job["steps"]:
            if step.get("name") == "Upload macOS Full Artifact":
                name_val = step.get("with", {}).get("name", "")
                assert "${{ matrix.arch_label }}" in name_val, (
                    "Full upload artifact name must include matrix.arch_label"
                )
                return
        pytest.fail("Upload macOS Full Artifact step not found")

    def test_lite_upload_has_arch_label(self, macos_job: dict):
        for step in macos_job["steps"]:
            if step.get("name") == "Upload macOS Lite Artifact":
                name_val = step.get("with", {}).get("name", "")
                assert "${{ matrix.arch_label }}" in name_val, (
                    "Lite upload artifact name must include matrix.arch_label"
                )
                return
        pytest.fail("Upload macOS Lite Artifact step not found")


class TestReleaseAssets:
    """Verify the release job references all four macOS artifacts."""

    def test_release_needs_both_jobs(self, workflow: dict):
        needs = workflow["jobs"]["create-release"]["needs"]
        assert "build-windows" in needs
        assert "build-macos" in needs

    def test_release_files_include_four_macos_variants(self, release_job: dict):
        # Find the release step that has files
        files_text = ""
        for step in release_job["steps"]:
            if "files" in step.get("with", {}):
                files_text = step["with"]["files"]
                break
        assert files_text, "Release step must specify files"
        # Must include all 4 macOS variants
        for label in ("ARM64", "x86-64"):
            for variant in ("Full", "Lite"):
                assert label in files_text, (
                    f"Release files missing {label} in artifact path"
                )
                assert variant in files_text, (
                    f"Release files missing {variant} in artifact path"
                )
        # Must still include Windows artifacts
        assert "Windows_Full" in files_text
        assert "Windows_Lite" in files_text

    def test_release_files_explicit_arm64_full(self, release_job: dict):
        files_text = ""
        for step in release_job["steps"]:
            if "files" in step.get("with", {}):
                files_text = step["with"]["files"]
                break
        assert "ARM64" in files_text and "Full" in files_text

    def test_release_files_explicit_arm64_lite(self, release_job: dict):
        files_text = ""
        for step in release_job["steps"]:
            if "files" in step.get("with", {}):
                files_text = step["with"]["files"]
                break
        assert "ARM64" in files_text and "Lite" in files_text

    def test_release_files_explicit_x86_64_full(self, release_job: dict):
        files_text = ""
        for step in release_job["steps"]:
            if "files" in step.get("with", {}):
                files_text = step["with"]["files"]
                break
        assert "x86-64" in files_text and "Full" in files_text

    def test_release_files_explicit_x86_64_lite(self, release_job: dict):
        files_text = ""
        for step in release_job["steps"]:
            if "files" in step.get("with", {}):
                files_text = step["with"]["files"]
                break
        assert "x86-64" in files_text and "Lite" in files_text


class TestDocumentation:
    """Verify README and BUILD docs mention x86_64 and macos-15-large."""

    def test_readme_mentions_x86_64(self, readme: str):
        assert "x86_64" in readme or "x86-64" in readme, (
            "README must mention x86_64 for Intel Mac users"
        )

    def test_readme_mentions_arm64(self, readme: str):
        assert "ARM64" in readme or "arm64" in readme, (
            "README must mention ARM64 for Apple Silicon users"
        )

    def test_readme_mentions_intel_apple_silicon_choice(self, readme: str):
        assert ("Intel" in readme or "intel" in readme) and (
            "Apple Silicon" in readme or "Apple" in readme
        ), "README should guide Intel vs Apple Silicon users"

    def test_build_md_mentions_x86_64(self, build_md: str):
        assert "x86_64" in build_md or "x86-64" in build_md, (
            "BUILD.md must mention x86_64"
        )

    def test_build_md_mentions_wechat_mp_tools_target_arch(self, build_md: str):
        assert "WECHAT_MP_TOOLS_TARGET_ARCH" in build_md, (
            "BUILD.md must document WECHAT_MP_TOOLS_TARGET_ARCH for local builds"
        )

    def test_build_md_mentions_macos_15_large(self, build_md: str):
        assert "macos-15-large" in build_md, (
            "BUILD.md must explain that CI uses macos-15-large for Intel builds"
        )

    def test_build_md_documents_intel_local_build(self, build_md: str):
        assert "x86_64" in build_md and "WECHAT_MP_TOOLS_TARGET_ARCH=x86_64" in build_md, (
            "BUILD.md must show how to build x86_64 locally on Intel Mac"
        )
