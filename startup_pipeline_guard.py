#!/usr/bin/env python3
"""
Windows 开机守护脚本：
- 每次系统启动时由任务计划程序调用本脚本
- 若距离上次触发已超过设定阈值（默认 24 小时），则调用 pipeline_runner.py
- 若未超过阈值，则直接退出，避免重复执行
"""

import os
import sys
import json
import time
import subprocess
import argparse
from datetime import datetime


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
SYSTEM_DIR = os.path.join(DATA_DIR, "system")
STATE_FILE = os.path.join(SYSTEM_DIR, "startup_guard_state.json")
LOG_FILE = os.path.join(SYSTEM_DIR, "startup_guard.log")
PIPELINE_FILE = os.path.join(PROJECT_ROOT, "pipeline_runner.py")

DEFAULT_STAGE = "all"
DEFAULT_THRESHOLD_HOURS = 24

os.makedirs(SYSTEM_DIR, exist_ok=True)


def now_ts() -> int:
    return int(time.time())


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def append_log(message: str) -> None:
    line = f"[{now_str()}] {message}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def should_run(state: dict, threshold_seconds: int, force: bool) -> tuple[bool, str]:
    if force:
        return True, "已使用 --force，忽略时间阈值。"

    last_triggered_at = int(state.get("last_triggered_at", 0) or 0)
    if last_triggered_at <= 0:
        return True, "未发现历史触发记录，本次允许执行。"

    elapsed = now_ts() - last_triggered_at
    if elapsed >= threshold_seconds:
        hours = elapsed / 3600
        return True, f"距离上次触发已过去 {hours:.2f} 小时，超过阈值。"

    remaining = threshold_seconds - elapsed
    remaining_hours = remaining / 3600
    return False, f"距离上次触发未满阈值，还需等待约 {remaining_hours:.2f} 小时。"


def build_command(stage: str, debug: bool) -> list[str]:
    command = [sys.executable, PIPELINE_FILE, "--stage", stage]
    if debug:
        command.append("--debug")
    return command


def run_pipeline(stage: str, debug: bool) -> int:
    command = build_command(stage, debug)
    append_log(f"准备执行管线: {' '.join(command)}")
    result = subprocess.run(command, cwd=PROJECT_ROOT)
    append_log(f"管线执行结束，退出码: {result.returncode}")
    return int(result.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Windows 启动时的 Mirror 管线守护脚本")
    parser.add_argument("--stage", default=DEFAULT_STAGE, help="传递给 pipeline_runner.py 的阶段参数")
    parser.add_argument("--threshold-hours", type=float, default=DEFAULT_THRESHOLD_HOURS, help="距离上次触发超过多少小时才再次执行")
    parser.add_argument("--force", action="store_true", help="忽略阈值检查，强制执行")
    parser.add_argument("--debug", action="store_true", help="调用 pipeline_runner.py 时附加 --debug")
    args = parser.parse_args()

    if not os.path.exists(PIPELINE_FILE):
        append_log(f"[FATAL] 找不到 pipeline_runner.py: {PIPELINE_FILE}")
        return 1

    threshold_seconds = max(int(args.threshold_hours * 3600), 0)
    state = load_state()

    decision, reason = should_run(state, threshold_seconds, args.force)
    append_log(reason)
    if not decision:
        return 0

    state["last_triggered_at"] = now_ts()
    state["last_triggered_at_str"] = now_str()
    state["last_requested_stage"] = args.stage
    save_state(state)

    exit_code = run_pipeline(args.stage, args.debug)

    state["last_exit_code"] = exit_code
    state["last_finished_at"] = now_ts()
    state["last_finished_at_str"] = now_str()
    save_state(state)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
