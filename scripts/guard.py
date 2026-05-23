"""启动守护脚本 — 基于时间阈值的自动调度器，支持日常/周报双轨"""

import os
import sys
import json
import time
import logging
import subprocess
import argparse
from datetime import datetime

from .config import PipelineConfig

logger = logging.getLogger(__name__)


class StartupGuard:
    """定时守护器：按日常(Stage 1-3) / 周报(Stage 1-5) 双轨调度"""

    def __init__(self, config: PipelineConfig = None):
        self._cfg = config or PipelineConfig()
        self._state_file = os.path.join(self._cfg.system_dir, "startup_guard_state.json")
        self._log_file = os.path.join(self._cfg.system_dir, "startup_guard.log")
        self._pipeline_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "pipeline_runner.py")

    # ==================== main entry ====================

    def run(self, daily_hours: float = 24, weekly_hours: float = 168,
            force: bool = False, debug: bool = False) -> int:
        state = self._load_state()
        stage, reason = self._should_run(state, daily_hours * 3600, weekly_hours * 3600, force)

        if not stage:
            return 0

        self._log(f"触发: {reason}")
        now = int(time.time())
        state["last_daily_triggered_at"] = now
        if stage == "all":
            state["last_weekly_triggered_at"] = now
        self._save_state(state)

        exit_code = self._exec_pipeline(stage, debug)

        state["last_exit_code"] = exit_code
        state["last_finished_at"] = int(time.time())
        self._save_state(state)
        return exit_code

    # ==================== decision logic ====================

    def _should_run(self, state: dict, daily_sec: float, weekly_sec: float, force: bool) -> tuple:
        if force:
            return "all", "--force 触发全管线"
        now = int(time.time())
        if now - int(state.get("last_weekly_triggered_at", 0)) >= weekly_sec:
            return "all", "周报阈值触发 Stage 1-5"
        if now - int(state.get("last_daily_triggered_at", 0)) >= daily_sec:
            return "daily", "日常阈值触发 Stage 1-3"
        return (None, "未达阈值")

    # ==================== execution ====================

    def _exec_pipeline(self, stage: str, debug: bool) -> int:
        cmd = [sys.executable, self._pipeline_file, "--stage", stage]
        if debug:
            cmd.append("--debug")
        self._log(f"执行: {' '.join(cmd)}")

        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW

        result = subprocess.run(cmd, cwd=os.path.dirname(os.path.dirname(__file__)), **kwargs)
        self._log(f"退出码: {result.returncode}")
        return int(result.returncode)

    # ==================== state I/O ====================

    def _load_state(self) -> dict:
        if not os.path.exists(self._state_file):
            return {}
        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_state(self, state: dict):
        with open(self._state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def _log(self, message: str):
        line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
        with open(self._log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        logger.info(message)

    # ==================== CLI ====================

    @classmethod
    def main(cls):
        logging.basicConfig(level=logging.INFO, format="[%(levelname)-5s] %(message)s")
        logging.getLogger("httpx").setLevel(logging.WARNING)

        parser = argparse.ArgumentParser(description="Mirror 蒸馏守护脚本 (日常/周报双轨)")
        parser.add_argument("--daily-hours", type=float, default=24, help="日常周期 (h)")
        parser.add_argument("--weekly-hours", type=float, default=168, help="周报周期 (h)")
        parser.add_argument("--force", action="store_true", help="忽略阈值强制全管线")
        parser.add_argument("--debug", action="store_true", help="传递 debug 标志")
        args = parser.parse_args()

        guard = cls()
        sys.exit(guard.run(
            daily_hours=args.daily_hours,
            weekly_hours=args.weekly_hours,
            force=args.force,
            debug=args.debug,
        ))


if __name__ == "__main__":
    StartupGuard.main()
