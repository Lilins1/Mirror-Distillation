#!/usr/bin/env python3
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

os.makedirs(SYSTEM_DIR, exist_ok=True)

def now_ts() -> int: return int(time.time())
def now_str() -> str: return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def append_log(message: str) -> None:
    line = f"[{now_str()}] {message}"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def load_state() -> dict:
    if not os.path.exists(STATE_FILE): return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except: return {}

def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def should_run(state: dict, daily_sec: int, weekly_sec: int, force: bool) -> tuple[str, str]:
    if force: return "all", "已使用 --force，触发全管线执行。"
    
    now = now_ts()
    last_weekly = int(state.get("last_weekly_triggered_at", 0))
    last_daily = int(state.get("last_daily_triggered_at", 0))
    
    # 优先判断是否满足周报阶段 (全管线)
    if now - last_weekly >= weekly_sec:
        return "all", f"距离上次周报已超过阈值，触发 Stage 1-5 全管线。"
        
    # 其次判断是否满足日常阶段 (Stage 1-3)
    if now - last_daily >= daily_sec:
        return "daily", f"距离上次日常采集已超过阈值，触发 Stage 1-3。"
        
    return None, "尚未达到任何时间阈值，跳过执行。"

def run_pipeline(stage: str, debug: bool) -> int:
    command = [sys.executable, PIPELINE_FILE, "--stage", stage]
    if debug: command.append("--debug")
    append_log(f"准备执行管线: {' '.join(command)}")
    
    kwargs = {}
    # ✅ Windows 平台防弹窗黑魔法：创建无窗口进程
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000 # CREATE_NO_WINDOW
        
    result = subprocess.run(command, cwd=PROJECT_ROOT, **kwargs)
    append_log(f"管线执行结束，退出码: {result.returncode}")
    return int(result.returncode)

def main() -> int:
    parser = argparse.ArgumentParser(description="Mirror 蒸馏守护脚本 (支持日常/周报双轨)")
    parser.add_argument("--daily-hours", type=float, default=24, help="日常数据采集(阶段1-3)的触发周期")
    parser.add_argument("--weekly-hours", type=float, default=168, help="生成认知镜像(阶段4-5)的触发周期")
    parser.add_argument("--force", action="store_true", help="忽略阈值检查，强制执行全管线")
    parser.add_argument("--debug", action="store_true", help="调用 pipeline 时附加 debug 标志")
    args = parser.parse_args()

    state = load_state()
    stage_to_run, reason = should_run(state, args.daily_hours * 3600, args.weekly_hours * 3600, args.force)
    
    # 每次运行即便跳过，也不要疯狂写日志，这里只在真正执行时记录。如果想要监控心跳可以取消注释。
    # append_log(reason) 
    
    if not stage_to_run:
        return 0
        
    append_log(f"触发判定: {reason}")
    now = now_ts()
    state["last_daily_triggered_at"] = now
    if stage_to_run == "all":
        state["last_weekly_triggered_at"] = now
    save_state(state)

    exit_code = run_pipeline(stage_to_run, args.debug)
    
    state["last_exit_code"] = exit_code
    state["last_finished_at"] = now_ts()
    save_state(state)
    return exit_code

if __name__ == "__main__":
    sys.exit(main())