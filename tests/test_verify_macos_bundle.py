"""Tests for scripts/verify_macos_bundle.py."""

from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Module under test — must exist before import works
SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import verify_macos_bundle as vmb  # type: ignore[import-untyped]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_run(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> Any:
    """Return a mock for subprocess.run that returns a CompletedProcess."""
    result = subprocess.CompletedProcess(
        args=[],
        stdout=stdout.encode(),
        stderr=stderr.encode(),
        returncode=returncode,
    )
    mock = MagicMock(return_value=result)
    return mock


# ms-playwright path segments (versioned dirs use globs in implementation)
PW_BROWSER_SEGS = ("chromium-", "chrome-mac-")
PW_BROWSER_EXE = "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
PW_HEADLESS_SEGS = ("chromium_headless_shell-", "chrome-headless-shell-mac-")
PW_HEADLESS_EXE = "chrome-headless-shell"


def _fake_bundle(
    tmp_path: Path,
    has_main: bool = True,
    has_dylib: bool = True,
    has_chromium_browser: bool = False,
    has_headless_shell: bool = False,
    exe_name: str | None = None,
    plist_cfbe: str | None = None,
    chromium_dylib_arch: str | None = None,
) -> Path:
    """Create a minimal .app tree with optional ms-playwright Chromium."""
    app = tmp_path / "WeChat MP Tools.app"
    contents = app / "Contents"
    macos = contents / "MacOS"
    macos.mkdir(parents=True)
    if plist_cfbe is not None:
        with open(contents / "Info.plist", "wb") as f:
            plistlib.dump({"CFBundleExecutable": plist_cfbe}, f)
    else:
        (contents / "Info.plist").write_bytes(b"")
    if has_main:
        (macos / (exe_name or app.stem)).write_text("")
    if has_dylib:
        lib_dir = contents / "MacOS" / "_internal" / "lib"
        lib_dir.mkdir(parents=True)
        (lib_dir / "foo.dylib").write_text("")
    # ms-playwright Chromium tree
    pw = contents / "Frameworks" / "ms-playwright"
    if has_chromium_browser:
        seg = pw / PW_BROWSER_SEGS[0] / PW_BROWSER_SEGS[1]
        seg.mkdir(parents=True)
        exe_path = seg / PW_BROWSER_EXE
        exe_path.parent.mkdir(parents=True)
        exe_path.write_text("")
    if has_headless_shell:
        seg = pw / PW_HEADLESS_SEGS[0] / PW_HEADLESS_SEGS[1]
        seg.mkdir(parents=True)
        (seg / PW_HEADLESS_EXE).write_text("")
    if chromium_dylib_arch is not None:
        pw.mkdir(parents=True, exist_ok=True)
        (pw / "libfake.dylib").write_text("")
    return app


# ---------------------------------------------------------------------------
# 1. Parsing lipo -archs output
# ---------------------------------------------------------------------------

class TestParseLipoArches:
    def test_single_arch(self):
        assert vmb._parse_lipo_arches("x86_64\n") == {"x86_64"}

    def test_multiple_arches(self):
        assert vmb._parse_lipo_arches("x86_64 arm64\n") == {"x86_64", "arm64"}

    def test_empty_output(self):
        assert vmb._parse_lipo_arches("") == set()

    def test_trailing_whitespace(self):
        assert vmb._parse_lipo_arches("  arm64  \n") == {"arm64"}


# ---------------------------------------------------------------------------
# 2. architectures(path) -> set[str]
# ---------------------------------------------------------------------------

class TestArchitectures:
    def test_returns_lipo_output(self):
        mock_run = _make_run(stdout="arm64")
        with patch.object(vmb, "_run", mock_run):
            result = vmb.architectures(Path("/fake/binary"))
        assert result == {"arm64"}
        mock_run.assert_called_once()

    def test_returns_empty_on_failure(self):
        mock_run = _make_run(returncode=1, stderr="not a fat file")
        with patch.object(vmb, "_run", mock_run):
            result = vmb.architectures(Path("/fake/binary"))
        assert result == set()

    def test_returns_empty_on_lipo_launch_failure(self):
        """OSError from subprocess.run (e.g. lipo not found) yields empty set."""
        with patch.object(vmb, "_run", side_effect=OSError("no lipo")):
            result = vmb.architectures(Path("/fake/binary"))
        assert result == set()


# ---------------------------------------------------------------------------
# 3. Requiring the main executable
# ---------------------------------------------------------------------------

class TestMainExecutableRequired:
    def test_missing_main_exe_fails(self, tmp_path):
        app = _fake_bundle(tmp_path, has_main=False)
        mock_run = _make_run(stdout="arm64")
        with patch.object(vmb, "_run", mock_run):
            errs = vmb.verify(app, "arm64")
        assert any("main executable" in e.lower() for e in errs)

    def test_main_exe_wrong_arch_fails(self, tmp_path):
        app = _fake_bundle(tmp_path)
        mock_run = _make_run(stdout="x86_64")
        with patch.object(vmb, "_run", mock_run):
            errs = vmb.verify(app, "arm64")
        assert any("arm64" in e and "main" in e.lower() for e in errs)

    def test_main_exe_correct_arch_passes(self, tmp_path):
        app = _fake_bundle(tmp_path)
        mock_run = _make_run(stdout="arm64")
        with patch.object(vmb, "_run", mock_run):
            errs = vmb.verify(app, "arm64")
        assert not any("main" in e.lower() for e in errs)

    def test_reads_cfbe_from_plist(self, tmp_path):
        """Main executable name is read from Info.plist CFBundleExecutable."""
        app = _fake_bundle(tmp_path, exe_name="custom_bin", plist_cfbe="custom_bin")
        mock_run = _make_run(stdout="arm64")
        with patch.object(vmb, "_run", mock_run):
            errs = vmb.verify(app, "arm64")
        assert not any("main" in e.lower() for e in errs)


# ---------------------------------------------------------------------------
# 4. Requiring a .dylib / .so native extension match
# ---------------------------------------------------------------------------

class TestNativeExtensionRequired:
    def test_no_native_extensions_fails(self, tmp_path):
        app = _fake_bundle(tmp_path, has_dylib=False)
        mock_run = _make_run(stdout="arm64")
        with patch.object(vmb, "_run", mock_run):
            errs = vmb.verify(app, "arm64")
        assert any("native" in e.lower() or ".dylib" in e.lower() or ".so" in e.lower() for e in errs)

    def test_dylib_wrong_arch_fails(self, tmp_path):
        app = _fake_bundle(tmp_path)
        call_count = 0
        def side_effect(*a: Any, **kw: Any) -> subprocess.CompletedProcess:
            nonlocal call_count
            call_count += 1
            arch = "arm64" if call_count == 1 else "x86_64"
            return subprocess.CompletedProcess([], arch.encode(), b"", 0)
        with patch.object(vmb, "_run", side_effect=side_effect):
            errs = vmb.verify(app, "arm64")
        assert any("arm64" in e for e in errs)

    def test_dylib_correct_arch_passes(self, tmp_path):
        app = _fake_bundle(tmp_path)
        mock_run = _make_run(stdout="arm64")
        with patch.object(vmb, "_run", mock_run):
            errs = vmb.verify(app, "arm64")
        assert not any("native" in e.lower() for e in errs)

    def test_playwright_dylib_excluded_from_native_check(self, tmp_path):
        """A correct-arch .dylib inside ms-playwright must NOT satisfy
        the Python native extension requirement."""
        app = _fake_bundle(tmp_path, has_dylib=False, has_chromium_browser=True,
                           chromium_dylib_arch="arm64")
        mock_run = _make_run(stdout="arm64")
        with patch.object(vmb, "_run", mock_run):
            errs = vmb.verify(app, "arm64")
        assert any("native" in e.lower() for e in errs)

    def test_app_dylib_wrong_chromium_dylib_correct_still_fails(self, tmp_path):
        """Regression: app .dylib wrong-arch + Chromium .dylib correct-arch
        still fails the native extension check."""
        app = _fake_bundle(tmp_path, has_dylib=True, has_chromium_browser=True,
                           chromium_dylib_arch="arm64")
        call_count = 0
        def side_effect(*a: Any, **kw: Any) -> subprocess.CompletedProcess:
            nonlocal call_count
            call_count += 1
            # 1=main(arm64), 2=app_dylib(x86_64), 3+=chromium stuff(arm64)
            arch = "x86_64" if call_count == 2 else "arm64"
            return subprocess.CompletedProcess([], arch.encode(), b"", 0)
        with patch.object(vmb, "_run", side_effect=side_effect):
            errs = vmb.verify(app, "arm64")
        assert any("native" in e.lower() for e in errs)


# ---------------------------------------------------------------------------
# 5. Checking Chromium when bundled (ms-playwright layout)
# ---------------------------------------------------------------------------

class TestChromiumCheck:
    def test_browser_wrong_arch_fails(self, tmp_path):
        app = _fake_bundle(tmp_path, has_chromium_browser=True)
        call_count = 0
        def side_effect(*a: Any, **kw: Any) -> subprocess.CompletedProcess:
            nonlocal call_count
            call_count += 1
            arch = "x86_64" if call_count >= 3 else "arm64"
            return subprocess.CompletedProcess([], arch.encode(), b"", 0)
        with patch.object(vmb, "_run", side_effect=side_effect):
            errs = vmb.verify(app, "arm64")
        assert any("chromium" in e.lower() or "chrome" in e.lower() for e in errs)

    def test_browser_correct_arch_passes(self, tmp_path):
        app = _fake_bundle(tmp_path, has_chromium_browser=True)
        mock_run = _make_run(stdout="arm64")
        with patch.object(vmb, "_run", mock_run):
            errs = vmb.verify(app, "arm64")
        assert not any("chromium" in e.lower() for e in errs)

    def test_headless_shell_wrong_arch_fails(self, tmp_path):
        app = _fake_bundle(tmp_path, has_headless_shell=True)
        call_count = 0
        def side_effect(*a: Any, **kw: Any) -> subprocess.CompletedProcess:
            nonlocal call_count
            call_count += 1
            arch = "x86_64" if call_count >= 3 else "arm64"
            return subprocess.CompletedProcess([], arch.encode(), b"", 0)
        with patch.object(vmb, "_run", side_effect=side_effect):
            errs = vmb.verify(app, "arm64")
        assert any("headless" in e.lower() or "chromium" in e.lower() for e in errs)

    def test_headless_shell_correct_arch_passes(self, tmp_path):
        app = _fake_bundle(tmp_path, has_headless_shell=True)
        mock_run = _make_run(stdout="arm64")
        with patch.object(vmb, "_run", mock_run):
            errs = vmb.verify(app, "arm64")
        assert not any("headless" in e.lower() or "chromium" in e.lower() for e in errs)

    def test_no_playwright_bundled_no_error(self, tmp_path):
        app = _fake_bundle(tmp_path, has_chromium_browser=False, has_headless_shell=False)
        mock_run = _make_run(stdout="arm64")
        with patch.object(vmb, "_run", mock_run):
            errs = vmb.verify(app, "arm64")
        assert not any("chromium" in e.lower() for e in errs)

    def test_require_chromium_fails_when_missing(self, tmp_path):
        app = _fake_bundle(tmp_path, has_chromium_browser=False, has_headless_shell=False)
        with patch.object(vmb, "_run", _make_run(stdout="arm64")):
            errs = vmb.verify(app, "arm64", require_chromium=True)
        assert any("requires bundled chromium" in e.lower() for e in errs)

    def test_require_chromium_passes_when_present(self, tmp_path):
        app = _fake_bundle(tmp_path, has_chromium_browser=True)
        with patch.object(vmb, "_run", _make_run(stdout="arm64")):
            errs = vmb.verify(app, "arm64", require_chromium=True)
        assert not errs

    def test_all_chromium_executables_checked(self, tmp_path):
        """Both browser and headless shell are checked when present."""
        app = _fake_bundle(tmp_path, has_chromium_browser=True, has_headless_shell=True)
        call_count = 0
        def side_effect(*a: Any, **kw: Any) -> subprocess.CompletedProcess:
            nonlocal call_count
            call_count += 1
            # 1=main, 2=dylib, 3=browser(arm64), 4=headless(x86_64)
            arch = "x86_64" if call_count >= 4 else "arm64"
            return subprocess.CompletedProcess([], arch.encode(), b"", 0)
        with patch.object(vmb, "_run", side_effect=side_effect):
            errs = vmb.verify(app, "arm64")
        assert any("headless" in e.lower() for e in errs)


# ---------------------------------------------------------------------------
# 6. Universal binary acceptance
# ---------------------------------------------------------------------------

class TestUniversalBinary:
    def test_universal_binary_containing_requested_arch_passes(self, tmp_path):
        app = _fake_bundle(tmp_path)
        mock_run = _make_run(stdout="x86_64 arm64")
        with patch.object(vmb, "_run", mock_run):
            errs = vmb.verify(app, "arm64")
        assert errs == []

    def test_universal_binary_wrong_single_request_fails(self, tmp_path):
        app = _fake_bundle(tmp_path)
        call_count = 0
        def side_effect(*a: Any, **kw: Any) -> subprocess.CompletedProcess:
            nonlocal call_count
            call_count += 1
            return subprocess.CompletedProcess([], b"x86_64\n", b"", 0)
        with patch.object(vmb, "_run", side_effect=side_effect):
            errs = vmb.verify(app, "arm64")
        assert len(errs) > 0


# ---------------------------------------------------------------------------
# 7. Unsupported requested architecture rejection (before subprocess)
# ---------------------------------------------------------------------------

class TestUnsupportedArch:
    def test_rejects_unsupported_arch(self, tmp_path):
        app = _fake_bundle(tmp_path)
        mock_run = _make_run(stdout="arm64")
        with patch.object(vmb, "_run", mock_run) as m:
            errs = vmb.verify(app, "i386")
        assert any("unsupported" in e.lower() for e in errs)
        m.assert_not_called()

    def test_rejects_empty_arch(self, tmp_path):
        app = _fake_bundle(tmp_path)
        with patch.object(vmb, "_run") as m:
            errs = vmb.verify(app, "")
        assert any("unsupported" in e.lower() for e in errs)
        m.assert_not_called()


# ---------------------------------------------------------------------------
# 8. CLI integration
# ---------------------------------------------------------------------------

class TestCLI:
    def test_exit_zero_on_success(self, tmp_path, capsys):
        app = _fake_bundle(tmp_path)
        mock_run = _make_run(stdout="arm64")
        with patch.object(vmb, "_run", mock_run):
            with pytest.raises(SystemExit) as exc_info:
                vmb.main([str(app), "arm64"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "OK: bundle supports arm64" in captured.out
        assert captured.err == ""

    def test_exit_nonzero_on_failure(self, tmp_path, capsys):
        app = _fake_bundle(tmp_path, has_main=False)
        mock_run = _make_run(stdout="arm64")
        with patch.object(vmb, "_run", mock_run):
            with pytest.raises(SystemExit) as exc_info:
                vmb.main([str(app), "arm64"])
        assert exc_info.value.code != 0
        captured = capsys.readouterr()
        assert len(captured.err) > 0

    def test_missing_args_exits_2(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            vmb.main([])
        assert exc_info.value.code == 2

    def test_deterministic_output_order(self, tmp_path, capsys):
        """Errors are sorted for deterministic output."""
        app = _fake_bundle(tmp_path, has_main=False, has_dylib=False)
        with patch.object(vmb, "_run", _make_run(stdout="arm64")):
            with pytest.raises(SystemExit):
                vmb.main([str(app), "arm64"])
        captured = capsys.readouterr()
        lines = [l for l in captured.err.strip().split("\n") if l]
        assert lines == sorted(lines)
