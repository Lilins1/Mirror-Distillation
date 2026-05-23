"""Stage 1: Bilibili 历史采集器 — 增量拉取观看记录，构建全局视频索引"""

import os
import json
import random
import time
import asyncio
import logging
from datetime import datetime
from typing import Optional

import httpx
from bilibili_api import Credential

from .config import PipelineConfig
from .auth import BilibiliAuth
from .storage import DataStorage

logger = logging.getLogger(__name__)


class Stage1Collector:
    """从 Bilibili API 增量拉取观看历史"""

    def __init__(self, config: PipelineConfig):
        self._cfg = config
        self._auth = BilibiliAuth(config.account_dir, label="主账号")
        self._storage = DataStorage()

    # ==================== public API ====================

    async def run(self) -> None:
        cred = await self._auth.get_credential()
        pages = self._cfg.debug_max_pages if self._cfg.debug_mode else self._cfg.prod_max_pages
        if self._cfg.debug_mode:
            logger.info("[DEBUG] 调试模式，限制抓取上限")

        new_links, master_data = await self._fetch_history(cred, max_pages=pages)
        self._persist(new_links, master_data)

    # ==================== core logic ====================

    async def _fetch_history(self, cred: Credential, max_pages: int) -> tuple:
        """增量拉取历史记录，支持断点回退"""
        master_data = self._storage.load_json_or_default(self._cfg.master_index_file)
        last_run_time = self._get_file_mtime(self._cfg.master_index_file)

        time_gap = time.time() - last_run_time
        deep_scan = time_gap > self._cfg.deep_scan_interval
        if deep_scan:
            logger.info("距离上次抓取超过3天，触发深度重置扫描模式")

        new_links = {}
        cookies = {"SESSDATA": cred.sessdata, "bili_jct": cred.bili_jct, "buvid3": cred.buvid3}
        headers = {"User-Agent": self._random_ua(), "Referer": "https://www.bilibili.com/"}

        async with httpx.AsyncClient(cookies=cookies, headers=headers, timeout=15.0) as client:
            max_cursor = 0
            view_at_cursor = 0
            overlap_count = 0

            for page in range(1, max_pages + 1):
                page_success = False
                should_break = False

                for retry in range(3):
                    if retry > 0:
                        logger.info("重试第 %d 页 (第 %d/2 次)", page, retry)

                    try:
                        url = (
                            f"https://api.bilibili.com/x/web-interface/history/cursor"
                            f"?max={max_cursor}&view_at={view_at_cursor}&business=archive"
                        )
                        resp = await client.get(url)
                        res_json = resp.json()

                        if res_json.get("code") != 0:
                            should_break = True
                            break

                        history_list = res_json.get("data", {}).get("list", [])
                        if not history_list:
                            should_break = True
                            break

                        for item in history_list:
                            if item.get("history", {}).get("business") != "archive":
                                continue

                            bvid = item["history"].get("bvid")
                            view_time = item.get("view_at", 0)

                            # 断点衔接
                            if bvid in master_data and master_data[bvid]["metadata"]["view_at"] >= view_time:
                                overlap_count += 1
                                if not deep_scan and overlap_count >= 3:
                                    logger.info("连续匹配已知记录，断点衔接成功")
                                    should_break = True
                                    break
                                continue
                            else:
                                overlap_count = 0

                            if not bvid:
                                continue

                            progress_raw = item.get("progress") or item.get("history", {}).get("progress", 0)
                            progress = int(progress_raw) if progress_raw else 0
                            duration = item.get("duration", 0)

                            new_links[bvid] = {
                                "video_id": bvid,
                                "metadata": {
                                    "title": item.get("title", "未知"),
                                    "duration": duration,
                                    "progress": progress,
                                    "view_at": view_time,
                                    "author": item.get("author_name", ""),
                                    "author_mid": item.get("author_mid") or item.get("history", {}).get("mid", 0),
                                    "category": "",
                                    "description": "",
                                    "tags": [],
                                },
                                "cognitive_impact_factor": self._calc_base_cif(progress, duration),
                                "interaction_status": {"coin_count": 0, "is_favorited": False, "is_liked": False},
                            }

                        if should_break:
                            break

                        cursor = res_json.get("data", {}).get("cursor", {})
                        max_cursor = cursor.get("max", 0)
                        view_at_cursor = cursor.get("view_at", 0)
                        if max_cursor == 0:
                            should_break = True
                            break

                        await asyncio.sleep(random.uniform(3.0, 5.5))
                        page_success = True
                        break

                    except Exception as e:
                        logger.error("第 %d 页异常: %s", page, e)
                        if retry < 2:
                            await asyncio.sleep(3 ** (retry + 1))

                if should_break:
                    break
                if not page_success:
                    logger.warning("第 %d 页连续失败，停止抓取", page)
                    break

        logger.info("增量抓取完毕，发现 %d 个新观看记录", len(new_links))
        return new_links, master_data

    def _persist(self, new_links: dict, master_data: dict) -> None:
        if not new_links:
            logger.info("本次无新数据")
            return
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_file = os.path.join(self._cfg.stage1_dir, f"index_links_{timestamp}.json")
        self._storage.safe_save_json(new_links, archive_file)
        master_data.update(new_links)
        self._storage.safe_save_json(master_data, self._cfg.master_index_file)
        logger.info("Master 库已更新，总节点: %d", len(master_data))

    # ==================== helpers ====================

    @staticmethod
    def _calc_base_cif(progress: int, duration: int) -> float:
        if duration <= 0:
            return 0.1
        if progress == -1:
            return 1.5
        return round((progress / duration) * 1.5, 3)

    @staticmethod
    def _random_ua() -> str:
        return random.choice([
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
        ])

    @staticmethod
    def _get_file_mtime(path: str) -> float:
        return os.path.getmtime(path) if os.path.exists(path) else 0.0
