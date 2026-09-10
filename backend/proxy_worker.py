"""
独立运行的 mitmproxy 代理工作进程
以独立子进程运行，确保与 Flask 主服务完全隔离，
实现瞬间启动、可靠关闭与内核级端口自动释放，杜绝线程假死与端口冲突。
"""
import sys
import os
import argparse
import asyncio
from pathlib import Path

# 确保能正确引用项目根目录模块
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from mitmproxy.tools.dump import DumpMaster
from mitmproxy import options
from backend.mitm_proxy import ChannelsAddon, prepare_mitm_confdir


def main():
    parser = argparse.ArgumentParser(description="Channels & Official Accounts MITM Proxy Worker")
    parser.add_argument("--proxy-worker", action="store_true", help="Flag for frozen executable")
    parser.add_argument("--port", type=int, default=5202, help="Proxy port")
    parser.add_argument("--confdir", type=str, default="", help="Mitmproxy confdir")
    parser.add_argument("--upstream", type=str, default="", help="Upstream VPN proxy (e.g. http://127.0.0.1:7897)")
    args, _ = parser.parse_known_args()

    confdir = args.confdir or str(prepare_mitm_confdir())

    async def _serve():
        opts_kwargs = {
            "listen_host": "127.0.0.1",
            "listen_port": args.port,
            "confdir": confdir,
            "ssl_insecure": True,
        }
        if args.upstream:
            opts_kwargs["mode"] = [f"upstream:{args.upstream}"]
        else:
            opts_kwargs["mode"] = [f"regular@{args.port}"]

        opts = options.Options(**opts_kwargs)
        master = DumpMaster(opts, with_termlog=False, with_dumper=False)
        for a in list(master.addons.chain):
            if type(a).__name__ == "ErrorCheck":
                try:
                    master.addons.remove(a)
                except Exception:
                    pass

        def on_ready():
            print("WORKER_READY", flush=True)

        master.addons.add(ChannelsAddon(ready_callback=on_ready))
        await master.run()

    try:
        asyncio.run(_serve())
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception as e:
        print(f"WORKER_ERROR: {e}", flush=True)


if __name__ == "__main__":
    main()
