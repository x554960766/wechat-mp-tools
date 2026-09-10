"""macOS 微信 PC 客户端自动化全流程执行入口。
按顺序编排：
Step 1: 启动与就绪确认
Step 2: AppleScript 搜索关键词并打开搜一搜 Web 窗口
Step 3: Vision OCR + 相对时间锚点识别文章卡片并点击
Step 4: 清理多余 Web 窗口并恢复状态
"""
from __future__ import annotations

import os
import sys

def ensure_virtualenv():
    """检测当前是否运行在虚拟环境 venv312 中。
    如果不是，并且检测到本地存在 venv312，则自动使用 venv312 的 python 解释器重载当前脚本！
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    if getattr(sys, 'frozen', False):
        return

    if sys.platform == 'win32':
        venv_python = os.path.join(project_root, 'venv312', 'Scripts', 'python.exe')
    else:
        venv_python = os.path.join(project_root, 'venv312', 'bin', 'python')
    if os.path.exists(venv_python):
        current_exe = os.path.abspath(sys.executable)
        target_exe = os.path.abspath(venv_python)
        if current_exe != target_exe:
            env = dict(os.environ)
            old_pypath = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{project_root}:{old_pypath}" if old_pypath else project_root
            args = [venv_python, "-m", "mac.main_mac"] + sys.argv[1:]
            os.execve(venv_python, args, env)




import argparse
import logging

from mac.steps.step1_launch import ensure_wechat_ready
from mac.steps.step2_search import search_and_open_web_window
from mac.steps.step3_open_article import find_and_click_article_mac
from mac.steps.step4_cleanup import cleanup_mac_wechat_windows
from mac.mac_input import human_sleep

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s][%(levelname)s][%(name)s] %(message)s"
)
logger = logging.getLogger("wechat_auto_mac")


def run_wechat_pc_flow_macos(
    keyword: str = "新京报",
    auto_cleanup: bool = True,
    random_pick: bool = False
) -> bool:
    """
    运行完整的 macOS 微信 UI 自动化凭证刷新流程。
    返回 True 表示流程执行成功。
    """
    logger.info("🚀 开始执行 macOS 微信 PC 端自动化流程 (目标关键词: %s)", keyword)
    try:
        # Step 1: 确保微信运行就绪
        main_win = ensure_wechat_ready()
        logger.info("Step 1 就绪: %s", main_win)

        # Step 2: 搜索关键词并打开搜一搜窗口
        web_win = search_and_open_web_window(keyword=keyword)
        logger.info("Step 2 搜一搜窗口打开: %s", web_win)
        human_sleep(0.8, 1.2)

        # Step 3: OCR 定位文章卡片并点击
        result = find_and_click_article_mac(
            web_win=web_win,
            max_scrolls=3,
            min_candidates=1,
            random_pick=random_pick,
            wait_cred_update_seconds=5.0
        )
        logger.info("Step 3 文章卡片点击完成: %s", result)

        return True
    except Exception as e:
        logger.error("❌ macOS 微信自动化流程执行异常: %s", e, exc_info=True)
        return False
    finally:
        if auto_cleanup:
            try:
                human_sleep(1.0, 1.5)
                cleanup_mac_wechat_windows()
            except Exception as clean_err:
                logger.warning("清理窗口时出现异常: %s", clean_err)


def main():
    parser = argparse.ArgumentParser(description="macOS 微信 PC 客户端 UI 自动化采集与凭证流水线")
    parser.add_argument("--keyword", "-k", default="新京报", help="单目标搜索关键词 (默认: 新京报)")
    parser.add_argument("--no-cleanup", action="store_true", help="执行完成后不自动关闭 Web 窗口")
    parser.add_argument("--random", action="store_true", help="随机点击候选文章卡片")
    parser.add_argument("--batch", "-b", nargs="+", help="批量指定公众号名称列表，例如: -b 新京报 人民日报 央视新闻")
    parser.add_argument("--all-saved", action="store_true", help="自动读取已收藏的全部公众号并进行批量同步")
    parser.add_argument("--file", "-f", help="从文本文件中读取公众号名称列表（每行一个）")
    parser.add_argument("--count", "-c", type=int, default=10, help="每个公众号拉取的文章数量 (默认: 10)")
    parser.add_argument("--batch-rest", type=int, default=30, help="每处理 N 个公众号深度休息一次 (默认: 30)")
    args = parser.parse_args()

    # 1. 批量模式：全部已收藏公众号
    if args.all_saved:
        from backend.accounts import _load_accounts
        from mac.batch_runner import run_batch_pipeline
        saved_accounts = _load_accounts()
        if not saved_accounts:
            logger.error("未找到已收藏的公众号，请先在系统添加公众号或使用 -b 指定名称！")
            sys.exit(1)
        res = run_batch_pipeline(
            accounts=saved_accounts,
            articles_per_account=args.count,
            batch_rest_every=args.batch_rest
        )
        sys.exit(0 if res["failed_count"] == 0 else 1)

    # 2. 批量模式：文件导入
    elif args.file:
        from mac.batch_runner import run_batch_pipeline
        file_path = Path(args.file)
        if not file_path.exists():
            logger.error("指定文件不存在: %s", args.file)
            sys.exit(1)
        lines = [line.strip() for line in file_path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.strip().startswith("#")]
        res = run_batch_pipeline(
            accounts=lines,
            articles_per_account=args.count,
            batch_rest_every=args.batch_rest
        )
        sys.exit(0 if res["failed_count"] == 0 else 1)

    # 3. 批量模式：命令行参数列表
    elif args.batch:
        from mac.batch_runner import run_batch_pipeline
        res = run_batch_pipeline(
            accounts=args.batch,
            articles_per_account=args.count,
            batch_rest_every=args.batch_rest
        )
        sys.exit(0 if res["failed_count"] == 0 else 1)

    # 4. 单号模式（默认）
    else:
        success = run_wechat_pc_flow_macos(
            keyword=args.keyword,
            auto_cleanup=not args.no_cleanup,
            random_pick=args.random
        )
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    ensure_virtualenv()
    main()

