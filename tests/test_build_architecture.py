import ast
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "wechat_mp_tools.spec"


def spec_source() -> str:
    return SPEC.read_text(encoding="utf-8")


def resolver_for(platform: str, environment: dict[str, str]):
    tree = ast.parse(spec_source())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "macos_target_arch"
    )
    module = ast.Module(body=[function], type_ignores=[])
    namespace = {
        "sys": SimpleNamespace(platform=platform),
        "os": SimpleNamespace(environ=environment),
    }
    exec(compile(module, str(SPEC), "exec"), namespace)
    return namespace["macos_target_arch"]


def test_target_arch_resolver_accepts_explicit_macos_architectures():
    assert resolver_for("darwin", {})() is None
    assert resolver_for("darwin", {"WECHAT_MP_TOOLS_TARGET_ARCH": "arm64"})() == "arm64"
    assert resolver_for("darwin", {"WECHAT_MP_TOOLS_TARGET_ARCH": "x86_64"})() == "x86_64"


def test_target_arch_resolver_rejects_unsupported_macos_architecture():
    resolver = resolver_for("darwin", {"WECHAT_MP_TOOLS_TARGET_ARCH": "universal2"})
    try:
        resolver()
    except ValueError as exc:
        assert "arm64 or x86_64" in str(exc)
    else:
        raise AssertionError("unsupported target architecture did not fail")


def test_target_arch_resolver_leaves_non_macos_platforms_native():
    resolver = resolver_for("win32", {"WECHAT_MP_TOOLS_TARGET_ARCH": "x86_64"})
    assert resolver() is None


def test_only_macos_pyinstaller_exe_uses_target_resolver():
    source = spec_source()
    assert source.count("target_arch=macos_target_arch()") == 1
    assert source.count("target_arch=None") == 2
