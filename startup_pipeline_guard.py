"""兼容入口：转发到 scripts.guard。

旧版自动任务/手动命令仍指向根目录的 startup_pipeline_guard.py，
保留此薄壳以避免路径失效，实际守护逻辑位于 scripts/guard.py。
"""

import os
import sys
import subprocess


def _resolve_python() -> str:
    exe = sys.executable
    if os.path.basename(exe).lower() == "pythonw.exe":
        alt = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.exists(alt):
            return alt
    return exe


def main() -> int:
    root = os.path.dirname(os.path.abspath(__file__))
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    cmd = [_resolve_python(), "-m", "scripts.guard", *sys.argv[1:]]
    return int(subprocess.run(cmd, cwd=root, env=env).returncode)


if __name__ == "__main__":
    sys.exit(main())
