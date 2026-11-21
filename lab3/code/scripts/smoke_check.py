"""用临时数据库和本机临时端口检查真实后端与 Qt 桌面窗口。"""

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


def available_port() -> int:
    """让操作系统选择一个本机可用端口。"""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_for_backend(process: subprocess.Popen, api_url: str) -> None:
    """限时等待服务可访问；本机请求不走用户代理。"""
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("后端启动进程已退出")
        try:
            with opener.open(f"{api_url}/health", timeout=1) as response:
                if json.load(response).get("status") == "ok":
                    return
        except (URLError, TimeoutError, ValueError):
            time.sleep(0.1)
    raise TimeoutError("等待后端启动超时")


@contextmanager
def running_backend() -> Iterator[tuple[str, dict[str, str]]]:
    """为窗口和接口验证共用一个临时服务，退出时回收进程和数据。"""
    port = available_port()
    api_url = f"http://127.0.0.1:{port}"

    with tempfile.TemporaryDirectory(prefix="shared-ledger-smoke-") as temporary:
        environment = os.environ.copy()
        environment.update(
            LEDGER_DATA_DIR=temporary,
            LEDGER_API_HOST="127.0.0.1",
            LEDGER_API_PORT=str(port),
            LEDGER_API_URL=api_url,
            NO_PROXY="127.0.0.1,localhost",
            no_proxy="127.0.0.1,localhost",
        )
        log_path = Path(temporary) / "server.log"
        with log_path.open("w", encoding="utf-8") as server_log:
            server = subprocess.Popen(  # pylint: disable=consider-using-with
                [sys.executable, "-m", "app.api"],
                env=environment,
                stdout=server_log,
                stderr=subprocess.STDOUT,
            )
            try:
                wait_for_backend(server, api_url)
                yield api_url, environment
            except (RuntimeError, TimeoutError, subprocess.SubprocessError):
                print(log_path.read_text(encoding="utf-8"), file=sys.stderr)
                raise
            finally:
                if server.poll() is None:
                    server.terminate()
                    try:
                        server.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait()


def main() -> None:
    """完成联通验证并关闭子进程，不保留测试业务数据。"""
    parser = argparse.ArgumentParser(description="项目启动联通检查")
    parser.add_argument("--screenshot", type=Path, help="可选：保存实际渲染的窗口")
    args = parser.parse_args()
    with running_backend() as (_, environment):
        command = [sys.executable, "-m", "app.ui", "--smoke-test"]
        if args.screenshot is not None:
            command.extend(["--screenshot", str(args.screenshot.resolve())])
        environment["QT_QPA_PLATFORM"] = "offscreen"
        subprocess.run(command, env=environment, check=True, timeout=15)
        print("PASS：真实 HTTP 服务、SQLite 和 Qt 桌面窗口联通，已自动退出。")


if __name__ == "__main__":
    main()
