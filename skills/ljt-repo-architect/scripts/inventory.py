#!/usr/bin/env python3
"""Repository inventory helper for the ljt-repo-architect skill.

Classifies Git-visible files into groups and emits deterministic, sorted JSON.
Uses only the Python standard library.

Output groups (first-party): entrypoints, backend, frontend, injection, docs, build, other.
Internal classification (excluded from output): vendored, ignored (returns None).
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import PurePosixPath

# Directories that are vendored/generated -- their contents are classified as
# "vendored" and never appear in first-party output groups.
VENDORED_PREFIXES = (
    PurePosixPath("backend/subtitle_remover"),
    PurePosixPath("injection_scripts/lib"),
    PurePosixPath("graphify-out"),
)

# Paths that should be treated as ignored (return None).
IGNORED_PREFIXES = (
    PurePosixPath("__pycache__"),
    PurePosixPath("data"),
    PurePosixPath("build"),
    PurePosixPath("dist"),
    PurePosixPath(".superpowers"),
)

# Entrypoint files at the repository root.
_ENTRYPOINT_FILES = {
    "main.py",
    "app.py",
    "wechat_mp_login.py",
    "wechat_mp_batch_downloader.py",
    "wechat_mp_article_fetcher.py",
    "wechat_mp_tools.spec",
}


def classify_path(path: PurePosixPath) -> str | None:
    """Classify a single POSIX path into a group name or None if ignored.

    Returns one of: entrypoints, backend, frontend, injection, docs, build,
    vendored, other, or None (ignored/generated).
    """
    parts = path.parts
    if not parts:
        return None

    # Check ignored prefixes first.
    for pfx in IGNORED_PREFIXES:
        if parts[0] == pfx.parts[0] and path.is_relative_to(pfx):
            return None
    # Also catch bare top-level ignored dirs.
    for pfx in IGNORED_PREFIXES:
        if len(pfx.parts) == 1 and parts[0] == pfx.parts[0]:
            return None

    # Check vendored prefixes.
    for pfx in VENDORED_PREFIXES:
        if path.is_relative_to(pfx):
            return "vendored"

    # Entrypoints: top-level entrypoint files.
    if len(parts) == 1 and parts[0] in _ENTRYPOINT_FILES:
        return "entrypoints"

    # Directory-based classification.
    top = parts[0]

    if top == "backend":
        return "backend"
    if top == "frontend":
        return "frontend"
    if top == "injection_scripts":
        return "injection"
    if top == "docs":
        return "docs"
    if top == ".github":
        return "build"
    if top == "tests":
        return "build"
    if top == "skills":
        return "build"

    return "other"


_FIRST_PARTY_GROUPS = {
    "entrypoints",
    "backend",
    "frontend",
    "injection",
    "docs",
    "build",
    "other",
}


def _git_files(root: str) -> list[str]:
    """Return sorted list of Git-visible relative paths (POSIX separators)."""
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        cwd=root,
    )
    if result.returncode != 0:
        print(f"git ls-files failed: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    lines = result.stdout.splitlines()
    # Normalize to POSIX separators and sort deterministically.
    return sorted(line.replace(os.sep, "/") for line in lines if line)


def build_inventory(root: str) -> dict:
    """Build the full inventory dict for a repository root."""
    root = os.path.realpath(root)
    files = _git_files(root)
    groups: dict[str, list[str]] = {}
    for f in files:
        p = PurePosixPath(f)
        group = classify_path(p)
        if group is None or group not in _FIRST_PARTY_GROUPS:
            continue
        groups.setdefault(group, []).append(f)
    # Sort each group for determinism.
    for g in groups:
        groups[g].sort()
    return {
        "root": root,
        "tracked_files": files,
        "groups": groups,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Repository inventory helper")
    parser.add_argument("--root", default=".", help="Repository root directory")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output JSON")
    args = parser.parse_args()

    inv = build_inventory(args.root)
    if args.json_output:
        json.dump(inv, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        print(json.dumps(inv, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
