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


def test_skill_enforces_source_evidence_and_rejects_filename_only_claims():
    skill = SKILL_FILE.read_text(encoding="utf-8")
    guide = (SKILL_DIR / "references/architecture-guide.md").read_text(encoding="utf-8")
    checklist = (SKILL_DIR / "references/quality-checklist.md").read_text(encoding="utf-8")

    for text in (skill, guide, checklist):
        lowered = text.lower()
        assert "source evidence" in lowered
        assert "filename-only" in lowered or "filename only" in lowered
        assert "unresolved" in lowered
