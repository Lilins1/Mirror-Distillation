"""Stage 2: 字幕/总结提取器 — 优先 B站官方AI总结，降级到字幕+SponsorBlock 过滤"""

import os
import json
import random
import time
import asyncio
import logging
from datetime import datetime
from typing import Optional

import httpx
from bilibili_api import Credential, video as b_video

from .config import PipelineConfig
from .auth import BilibiliAuth
from .storage import DataStorage

logger = logging.getLogger(__name__)


class SponsorBlockClient:
    """SponsorBlock API 客户端 — 查询视频广告片段"""

    def __init__(self, categories: list = None):
        self._categories = categories or ["sponsor", "selfpromo", "interaction"]

    async def get_segments(self, bvid: str) -> tuple[list, str, str]:
        """返回 (segments, status, message)"""
        try:
            cat_str = ",".join(f'"{c}"' for c in self._categories)
            url = f"https://sponsor.ajay.app/api/skipSegments?videoID={bvid}&categories=[{cat_str}]"
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    segments = [
                        {"start": round(s["segment"][0], 1), "end": round(s["segment"][1], 1),
                         "category": s["category"], "votes": s["votes"]}
                        for s in resp.json()
                    ]
                    if segments:
                        return segments, "success", f"发现{len(segments)}个广告片段"
                    return [], "no_segments", "无匹配广告标记"
                elif resp.status_code == 404:
                    return [], "no_segments", "未被社区标记"
                else:
                    return [], "api_error", f"状态码: {resp.status_code}"
        except httpx.TimeoutException:
            return [], "network_error", "请求超时"
        except httpx.NetworkError:
            return [], "network_error", "网络连接失败"
        except Exception as e:
            return [], "api_error", str(e)[:50]


class Stage2Extractor:
    """提取 Bilibili 视频的文本内容（AI总结优先，字幕降级）"""

    def __init__(self, config: PipelineConfig):
        self._cfg = config
        self._auth = BilibiliAuth(config.account_dir, label=config.stage2_account_label)
        self._storage = DataStorage()
        self._sponsor = SponsorBlockClient(config.sponsor_block_categories)

    # ==================== public API ====================

    async def run(self) -> None:
        await self._extract_all()

    async def run_parallel(self, done_event: Optional[asyncio.Event] = None) -> None:
        await self._extract_all()
        if done_event:
            done_event.set()

    async def run_batch(self, videos: dict, output_dir: str,
                        progress_file: str) -> dict:
        """处理自定义视频集合（供 UP Persona 复用）。
        返回 progress_cache。
        """
        os.makedirs(output_dir, exist_ok=True)
        cred = await self._auth.get_credential()
        progress = self._storage.load_progress(progress_file)

        # 过滤
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

        sem = asyncio.Semaphore(self._cfg.concurrency_limit)
        start_time = time.time()
        completed = [0]
        lock = asyncio.Lock()
        total = len(pending)

        await self._process_pending(cred, pending, progress, output_dir, progress_file)
        return progress

    # ==================== core logic ====================

    async def _extract_all(self) -> None:
        if not os.path.exists(self._cfg.master_enriched_file):
            logger.error("找不到提纯表: %s", self._cfg.master_enriched_file)
            return

        master_enriched = self._storage.load_json(self._cfg.master_enriched_file)
        progress_cache = self._storage.load_progress(self._cfg.subtitle_progress_file)
        cred = await self._auth.get_credential()

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
        await self._process_pending(cred, pending, progress_cache,
                                     self._cfg.subtitles_dir, self._cfg.subtitle_progress_file)

    async def _process_pending(self, cred: Credential, pending: list, progress_cache: dict,
                                output_dir: str, progress_file: str):
        sem = asyncio.Semaphore(self._cfg.concurrency_limit)
        start_time = time.time()
        completed = [0]
        lock = asyncio.Lock()
        total = len(pending)

        tasks = [
            self._process_one(cred, bvid, node, progress_cache, sem,
                              start_time, completed, lock, total,
                              output_dir=output_dir, progress_file=progress_file)
            for bvid, node in pending
        ]
        await asyncio.gather(*tasks)
        logger.info("批次完成，总用时 %.1f 分钟", (time.time() - start_time) / 60)

    async def _process_one(self, cred: Credential, bvid: str, node: dict,
                           progress_cache: dict, sem: asyncio.Semaphore,
                           start_time: float, completed: list, lock: asyncio.Lock, total: int,
                           output_dir: str = None, progress_file: str = None):
        async with sem:
            if bvid in progress_cache:
                return

            title = node["metadata"]["title"][:15]
            logger.info("处理: %s | %s...", bvid, title)

            v = b_video.Video(bvid=bvid, credential=cred)
            full_text = ""
            data_source = "none"
            has_data = False
            cid = None

            # SponsorBlock 预查询
            segments, sb_status, sb_msg = await self._sponsor.get_segments(bvid)
            if sb_status == "success":
                logger.info("  [SponsorBlock] %s", sb_msg)

            await asyncio.sleep(random.uniform(2.0, 4.0))

            # --- 策略1: B站官方 AI 总结 ---
            try:
                info = await v.get_info()
                cid = info.get("cid")
                up_mid = info.get("owner", {}).get("mid")
                api_url = f"https://api.bilibili.com/x/web-interface/view/conclusion/get?bvid={bvid}&cid={cid}&up_mid={up_mid}"
                req = await cred.request("GET", api_url)
                if req and req.get("code") == 0:
                    mr = req.get("data", {}).get("model_result", {})
                    if mr and mr.get("result_type") == 1:
                        blocks = [f"【AI 核心总结】: {mr.get('summary', '')}\n"]
                        for ol in mr.get("outline", []):
                            blocks.append(f"- {ol.get('title', '')}")
                            for part in ol.get("part_outline", []):
                                blocks.append(f"  * {part.get('content', '')}")
                        full_text = "\n".join(blocks)
                        has_data = True
                        data_source = "bilibili_ai_summary"
                        logger.info("  [AI总结] 命中官方总结")
            except Exception:
                pass

            await asyncio.sleep(random.uniform(2.5, 5.0))

            # --- 策略2: 官方字幕 + 广告过滤 ---
            if not has_data and cid is not None:
                try:
                    subs = await v.get_subtitle(cid)
                    if subs and subs.get("subtitles"):
                        sub_url = subs["subtitles"][0]["subtitle_url"]
                        if sub_url.startswith("//"):
                            sub_url = "https:" + sub_url
                        async with httpx.AsyncClient(timeout=10.0) as c:
                            sr = await c.get(sub_url)
                            if sr.status_code == 200:
                                body = sr.json().get("body", [])
                                filtered = self._filter_ads(body, segments)
                                removed = len(body) - len(filtered)
                                full_text = "\n".join(
                                    item.get("content", "").strip() for item in filtered
                                )
                                has_data = True
                                data_source = "bilibili_subtitle"
                                if removed:
                                    logger.info("  [字幕] 已过滤 %d 条广告", removed)
                except Exception as e:
                    logger.debug("  [字幕] 提取失败: %s", e)

            # --- 数据落地 ---
            output = {
                "video_id": bvid,
                "metadata": node["metadata"],
                "cognitive_impact_factor": node["cognitive_impact_factor"],
                "processing_status": {
                    "has_content": has_data,
                    "content_source": data_source,
                    "word_count": len(full_text),
                    "sponsor_block": {
                        "enabled": self._cfg.enable_sponsor_block,
                        "status": sb_status,
                        "message": sb_msg,
                        "ad_segments_found": len(segments),
                        "ad_segments": segments,
                    },
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

            await asyncio.sleep(random.uniform(25.0, 45.0))

    # ==================== helpers ====================

    @staticmethod
    def _filter_ads(subtitle_body: list, segments: list) -> list:
        if not segments:
            return subtitle_body
        return [
            item for item in subtitle_body
            if not any(
                item.get("from", 0) >= s["start"] and item.get("to", 0) <= s["end"]
                for s in segments
            )
        ]
