"""Verify macOS .app bundle architectures against a requested arch.

Public API
-----------
``architectures(path) -> set[str]``: return the set of CPU architectures
reported by ``lipo -archs`` for a single binary.
``verify(bundle, requested_arch) -> list[str]``: return a (possibly
empty) list of human-readable error strings.
``main(argv)``: CLI entry-point; calls ``sys.exit``.

Process execution is isolated behind the module-level ``_run`` callable
so that tests can inject a fake without touching ``subprocess``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Supported architectures — keep in sync with wechat_mp_tools.spec
SUPPORTED_ARCHES = {"arm64", "x86_64"}

# Injected process runner; default calls subprocess.run.
# Tests replace this with a mock.
_run = subprocess.run  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _parse_lipo_arches(stdout: str) -> set[str]:
    """Parse the newline-separated output of ``lipo -archs <binary>``."""
    return {a for a in stdout.split() if a}


def architectures(path: Path) -> set[str]:
    """Return the set of architectures for a single Mach-O binary."""
    try:
        proc = _run(
            ["lipo", "-archs", str(path)],
            capture_output=True,
        )
    except (OSError, FileNotFoundError):
        return set()
    if proc.returncode != 0:
        return set()
    return _parse_lipo_arches(proc.stdout.decode())

# ---------------------------------------------------------------------------
# Bundle discovery helpers
# ---------------------------------------------------------------------------

NATIVE_EXTENSIONS = {".dylib", ".so"}


def _find_main_executable(bundle: Path) -> Path | None:
    """Return the main executable path inside the bundle, or None."""
    exe_name = bundle.stem  # e.g. "WeChat MP Tools"
    candidate = bundle / "Contents" / "MacOS" / exe_name
    if candidate.is_file():
        return candidate
    return None


def _find_native_extensions(bundle: Path) -> list[Path]:
    """Find all .dylib / .so files anywhere under the bundle."""
    results: list[Path] = []
    for dirpath, _dirs, files in os.walk(bundle):
        dir_p = Path(dirpath)
        for fname in files:
            if any(fname.endswith(ext) for ext in NATIVE_EXTENSIONS):
                results.append(dir_p / fname)
    return results


def _find_chromium_executable(bundle: Path) -> Path | None:
    """Return the Chromium framework executable if bundled, else None."""
    for candidate in [
        bundle / "Contents" / "Frameworks" / "Chromium.app" / "Contents" / "MacOS" / "Chromium",
        bundle / "Contents" / "Frameworks" / "Chromium Embedded Framework.framework" / "Chromium Embedded Framework",
    ]:
        if candidate.is_file():
            return candidate
    return None

# ---------------------------------------------------------------------------
# Core verification
# ---------------------------------------------------------------------------


def verify(bundle: Path, requested_arch: str) -> list[str]:
    """Check *bundle* for *requested_arch* support.

    Returns a list of error strings.  An empty list means success.
    """
    errors: list[str] = []

    # --- 1. Validate requested arch (no subprocess needed) ---
    if requested_arch not in SUPPORTED_ARCHES:
        errors.append(
            f"Unsupported requested architecture '{requested_arch}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_ARCHES))}"
        )
        return sorted(errors)

    # --- 2. Main executable ---
    main_exe = _find_main_executable(bundle)
    if main_exe is None:
        errors.append("Main executable not found in bundle")
    else:
        main_arches = architectures(main_exe)
        if requested_arch not in main_arches:
            errors.append(
                f"Main executable {main_exe.name}: "
                f"architectures {main_arches} do not include '{requested_arch}'"
            )

    # --- 3. Native extensions (.dylib / .so) ---
    native_exts = _find_native_extensions(bundle)
    if not native_exts:
        errors.append(
            "No native extensions (.dylib/.so) found in bundle"
        )
    else:
        matching: list[str] = []
        for ext_path in native_exts:
            arches = architectures(ext_path)
            if requested_arch in arches:
                matching.append(str(ext_path))
        if not matching:
            errors.append(
                f"No native extension supports '{requested_arch}' "
                f"(checked {len(native_exts)} file(s))"
            )

    # --- 4. Chromium (optional) ---
    chromium_exe = _find_chromium_executable(bundle)
    if chromium_exe is not None:
        chrom_arches = architectures(chromium_exe)
        if requested_arch not in chrom_arches:
            errors.append(
                f"Chromium executable: "
                f"architectures {chrom_arches} do not include '{requested_arch}'"
            )

    return sorted(errors)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """CLI entry-point."""
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) != 2:
        print(f"Usage: {sys.argv[0]} <WeChat MP Tools.app> <arm64|x86_64>",
              file=sys.stderr)
        sys.exit(2)
    bundle_path = Path(argv[0])
    requested = argv[1]
    errors = verify(bundle_path, requested)
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        sys.exit(1)
    print(f"OK: bundle supports {requested}", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
