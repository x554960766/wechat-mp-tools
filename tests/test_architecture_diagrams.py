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


def test_build_diagram_does_not_claim_windows_architecture_verification():
    source = (DIAGRAMS / "build-flow.mmd").read_text(encoding="utf-8")
    assert "Verify Windows" not in source
    assert "x64 machine type" not in source
    assert "Every macOS artifact passed" in source


def test_build_diagram_separates_full_and_lite_chromium_verification():
    source = (DIAGRAMS / "build-flow.mmd").read_text(encoding="utf-8")
    assert "arm_lite --> arm_lite_build --> arm_lite_verify" in source
    assert "x86_lite --> x86_lite_build --> x86_lite_verify" in source
    assert "Chromium omitted" in source
    assert "arm_lite --> arm_build" not in source
    assert "x86_lite --> x86_build" not in source
