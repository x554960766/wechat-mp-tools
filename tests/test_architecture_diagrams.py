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
