from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
ARCHITECTURE = DOCS / "ARCHITECTURE.md"
LEARNING_PATH = DOCS / "CODE_LEARNING_PATH.md"
DIAGRAM_SOURCES = (
    "docs/diagrams/software-architecture.mmd",
    "docs/diagrams/application-flow.mmd",
    "docs/diagrams/build-flow.mmd",
)
UNFINISHED_MARKERS = ("TODO", "FIXME", "待补", "待完成", "未完成")


def _read(path: Path) -> str:
    assert path.is_file(), f"missing document: {path}"
    return path.read_text(encoding="utf-8")


def test_architecture_guide_covers_required_modules_and_concepts():
    text = _read(ARCHITECTURE)

    required_topics = {
        "main.py": "桌面入口",
        "app.py": "Flask 应用与蓝图注册",
        "蓝图": "API 分层",
        "backend/config.py": "配置、代理和存储路径",
        "backend/runtime.py": "源码/打包运行时",
        "backend/account_pool.py": "微信读书账号池",
        "backend/rss_scheduler.py": "RSS 后台调度",
        "backend/mitm_proxy.py": "视频号流量拦截",
        "backend/transcode.py": "FFmpeg 转码队列",
        "frontend/js/router.js": "前端哈希路由",
        "frontend/js/api.js": "前端 API 客户端",
        "injection_scripts/src/": "视频号页面注入",
        "PyInstaller": "桌面打包",
        "数据目录": "本地状态与文件",
    }
    for token, meaning in required_topics.items():
        assert token in text, f"architecture guide does not explain {meaning}: {token}"

    required_sections = (
        "模块地图",
        "请求流",
        "启动流",
        "后台工作",
        "存储",
        "构建与自动化",
        "扩展路径",
        "Bug 路由",
    )
    for heading in required_sections:
        assert heading in text, f"architecture guide missing section: {heading}"


def test_architecture_guide_describes_runtime_and_build_flows():
    text = _read(ARCHITECTURE)

    runtime_claims = {
 "backend/runtime.py": "resource_dir/app_dir/log_file/configure_runtime",
        "macOS": "Application Support/WeChat MP Tools",
        "Windows": "数据写在可执行文件旁",
        "backend/account_pool.py": "acquire/report/start_keepalive",
        "backend/rss_scheduler.py": "ThreadPoolExecutor/30 秒",
        "backend/transcode.py": "queue.Queue/单线程",
        "wechat_mp_tools.spec": "PyInstaller",
        ".github/workflows/build.yml": "GitHub Actions",
        "macOS ARM64": "ARM64",
        "macOS x86_64": "x86_64",
    }
    for token, meaning in runtime_claims.items():
        assert token in text, f"architecture guide does not explain {meaning}: {token}"


def test_both_documents_link_all_three_diagram_sources():
    for path in (ARCHITECTURE, LEARNING_PATH):
        text = _read(path)
        for diagram in DIAGRAM_SOURCES:
            assert f"]({diagram})" in text, f"{path.name} does not link {diagram}"


def test_learning_path_has_five_staged_exercises_with_commands_and_observations():
    text = _read(LEARNING_PATH)

    assert text.count("## 练习 1：") >= 1
    for number in range(2, 6):
        assert f"## 练习 {number}：" in text

    assert text.count("```bash") >= 5
    assert text.count("**预期观察**") >= 5
    for number in range(1, 6):
        assert f"练习 {number}" in text


def test_learning_path_names_exact_entry_points_and_debug_destinations():
    text = _read(LEARNING_PATH)
    required_paths = (
        "main.py",
        "app.py",
        "backend/config.py",
        "backend/runtime.py",
        "backend/account_pool.py",
        "backend/rss_scheduler.py",
        "backend/downloader.py",
        "backend/articles.py",
        "backend/channels.py",
        "backend/mitm_proxy.py",
        "backend/transcode.py",
        "frontend/js/router.js",
        "frontend/js/api.js",
        "injection_scripts/src/",
        "wechat_mp_tools.spec",
        ".github/workflows/build.yml",
    )
    for path in required_paths:
        assert path in text, f"learning path does not reference {path}"


def test_documents_do_not_contain_unfinished_work_markers():
    for path in (ARCHITECTURE, LEARNING_PATH):
        text = _read(path)
        for marker in UNFINISHED_MARKERS:
            assert marker not in text, f"{path.name} contains unfinished marker: {marker}"
