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
