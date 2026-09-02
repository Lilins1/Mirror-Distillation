"""Stage 2: 字幕/总结提取器 — 通过 bilibili-cli 获取 AI总结和字幕，降级到本地 Whisper"""

import os
import json
import random
import time
import asyncio
import subprocess
import logging
from datetime import datetime
from typing import Optional

from .config import PipelineConfig
from .storage import DataStorage
from .whisper_transcriber import WhisperTranscriber

logger = logging.getLogger(__name__)


_bili_missing_logged = False


def _bili_cli(args: list[str], timeout: int = 30) -> Optional[dict]:
    """调用 bilibili-cli 并返回 JSON envelope。失败返回 None（并记录原因）。"""
    global _bili_missing_logged
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        result = subprocess.run(
            ["bili"] + args + ["--json"],
            capture_output=True, timeout=timeout, env=env,
            encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        if not _bili_missing_logged:
            _bili_missing_logged = True
            logger.error("未找到 bilibili-cli (bili)，Stage2 字幕/AI总结提取将全部失败。"
                         "请先 `uv tool install bilibili-cli` 并激活 mirror_distill 环境")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("bilibili-cli 超时 (>%ds): %s", timeout, " ".join(args))
        return None
    except Exception as e:
        logger.warning("bilibili-cli 调用异常: %s", e)
        return None

    if result.returncode != 0:
        logger.warning("bilibili-cli 返回非零 (%d): %s | %s",
                       result.returncode, " ".join(args), (result.stderr or "")[:200])
        return None
    if not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        logger.warning("bilibili-cli 输出非 JSON: %s", e)
        return None


class Stage2Extractor:
    """提取 Bilibili 视频的文本内容（AI总结优先，字幕降级，Whisper 兜底）。

    通过 bilibili-cli 调用，无需手动管理 B站 认证。
    """

    def __init__(self, config: PipelineConfig):
        self._cfg = config
        self._storage = DataStorage()
        self._whisper = WhisperTranscriber(config) if config.enable_local_whisper else None

    # ==================== public API ====================

    async def run(self) -> None:
        await self._extract_all()

    async def run_parallel(self, done_event: Optional[asyncio.Event] = None) -> None:
        await self._extract_all()
        if done_event:
            done_event.set()

    async def run_batch(self, videos: dict, output_dir: str,
                        progress_file: str) -> dict:
        """处理自定义视频集合（供 UP Persona 复用）。返回 progress_cache。"""
        os.makedirs(output_dir, exist_ok=True)
        progress = self._storage.load_progress(progress_file)

        pending = []
        for bvid, node in videos.items():
            if bvid in progress:
                continue
            tags = node.get("metadata", {}).get("tags", [])
            if any("音乐" in str(t) for t in tags):
                continue
            pending.append((bvid, node))

        if self._cfg.debug_mode:
            pending = pending[:self._cfg.debug_item_limit]
        if not pending:
            logger.info("UP batch: 所有视频已处理")
            return progress

        logger.info("UP batch: 处理 %d 个视频 → %s", len(pending), output_dir)
        await self._process_pending(pending, progress, output_dir, progress_file)
        return progress

    # ==================== core logic ====================

    async def _extract_all(self) -> None:
        if not os.path.exists(self._cfg.master_enriched_file):
            logger.error("找不到提纯表: %s", self._cfg.master_enriched_file)
            return

        master_enriched = self._storage.load_json(self._cfg.master_enriched_file)
        progress_cache = self._storage.load_progress(self._cfg.subtitle_progress_file)

        pending = []
        skipped_music = 0
        for bvid, node in master_enriched.items():
            if bvid in progress_cache:
                continue
            tags = node.get("metadata", {}).get("tags", [])
            if any("音乐" in str(t) for t in tags):
                skipped_music += 1
                continue
            pending.append((bvid, node))

        if skipped_music:
            logger.info("已过滤 %d 个音乐视频", skipped_music)
        if self._cfg.debug_mode:
            pending = pending[:self._cfg.debug_item_limit]
        if not pending:
            logger.info("所有视频已处理完毕")
            return

        logger.info("开始处理 %d 个视频", len(pending))
        await self._process_pending(pending, progress_cache,
                                    self._cfg.subtitles_dir, self._cfg.subtitle_progress_file)

    async def _process_pending(self, pending: list, progress_cache: dict,
                                output_dir: str, progress_file: str):
        sem = asyncio.Semaphore(self._cfg.concurrency_limit)
        start_time = time.time()
        completed = [0]
        lock = asyncio.Lock()
        total = len(pending)

        tasks = [
            self._process_one(bvid, node, progress_cache, sem,
                              start_time, completed, lock, total,
                              output_dir=output_dir, progress_file=progress_file)
            for bvid, node in pending
        ]
        await asyncio.gather(*tasks)
        logger.info("批次完成，总用时 %.1f 分钟", (time.time() - start_time) / 60)

    async def _process_one(self, bvid: str, node: dict,
                           progress_cache: dict, sem: asyncio.Semaphore,
                           start_time: float, completed: list, lock: asyncio.Lock, total: int,
                           output_dir: str = None, progress_file: str = None):
        async with sem:
            if bvid in progress_cache:
                return

            title = node["metadata"]["title"][:15]
            logger.info("处理: %s | %s...", bvid, title)

            full_text = ""
            data_source = "none"
            has_data = False

            loop = asyncio.get_running_loop()

            # --- 策略1: B站官方 AI 总结 (bilibili-cli) ---
            data = await loop.run_in_executor(None, _bili_cli, ["video", bvid, "--ai"])
            if data and data.get("ok"):
                ai = data.get("data", {}).get("ai_summary", "")
                if ai:
                    full_text = f"【AI 核心总结】: {ai}"
                    has_data = True
                    data_source = "bilibili_ai_summary"
                    logger.info("  [AI总结] 命中官方总结 (%d字)", len(ai))

            await asyncio.sleep(random.uniform(2.5, 5.0))

            # --- 策略2: 官方字幕 (bilibili-cli) ---
            if not has_data:
                data = await loop.run_in_executor(None, _bili_cli, ["video", bvid, "--subtitle"])
                if data and data.get("ok"):
                    sub = data.get("data", {}).get("subtitle", {})
                    if sub.get("available") and sub.get("text", "").strip():
                        full_text = sub["text"]
                        has_data = True
                        data_source = "bilibili_subtitle"
                        logger.info("  [字幕] 获取成功 (%d字)", len(full_text))

            # --- 策略3: 本地 Whisper 兜底 ---
            if not has_data and self._whisper:
                try:
                    text = await self._whisper.transcribe(bvid) or ""
                    if text.strip():
                        full_text = text
                        has_data = True
                        data_source = "local_whisper"
                    else:
                        logger.warning("  [Whisper] 转录结果为空")
                except Exception as e:
                    logger.warning("  [Whisper] 转录异常: %s", e)

            # --- 数据落地 ---
            output = {
                "video_id": bvid,
                "metadata": node["metadata"],
                "cognitive_impact_factor": node["cognitive_impact_factor"],
                "processing_status": {
                    "has_content": has_data,
                    "content_source": data_source,
                    "word_count": len(full_text),
                },
                "full_text": full_text,
            }

            out_path = os.path.join(output_dir or self._cfg.subtitles_dir, f"{bvid}.json")
            self._storage.safe_save_json(output, out_path)
            progress_cache[bvid] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._storage.save_progress(progress_cache, progress_file or self._cfg.subtitle_progress_file)

            # 进度报告
            async with lock:
                completed[0] += 1
                elapsed = time.time() - start_time
                avg = elapsed / completed[0]
                remaining = (total - completed[0]) * avg
                pct = (completed[0] / total) * 100
                logger.info("  [PROGRESS] %d/%d (%.1f%%) | 预计剩余: %.1f分钟",
                            completed[0], total, pct, remaining / 60)

            # 防风控：轻度随机间隔（bilibili-cli 自身已有请求节奏，无需长时间休眠）
            await asyncio.sleep(random.uniform(1.0, 3.0))
