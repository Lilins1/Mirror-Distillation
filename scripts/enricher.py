"""Stage 1.5: CIF 提纯器 — 获取视频元数据、标签、互动状态，重新计算认知影响因子"""

import json
import random
import asyncio
import logging
from datetime import datetime

import httpx

from .config import PipelineConfig
from .auth import BilibiliAuth
from .storage import DataStorage

logger = logging.getLogger(__name__)


class Stage1Enricher:
    """对 master_index 中的视频节点进行多维信息提纯与 CIF 重算"""

    def __init__(self, config: PipelineConfig):
        self._cfg = config
        self._auth = BilibiliAuth(config.account_dir, label="主账号")
        self._storage = DataStorage()
        self._author_relation_cache: dict = {}

    # ==================== public API ====================

    async def run(self) -> None:
        if not self._has_input():
            logger.error("找不到基础拓扑表: %s", self._cfg.master_index_file)
            return

        master_index = self._storage.load_json(self._cfg.master_index_file)
        progress_cache = self._storage.load_progress(self._cfg.enrich_progress_file)
        master_enriched = self._storage.load_json_or_default(self._cfg.master_enriched_file)

        # 过滤未处理节点
        pending = [(bvid, n) for bvid, n in master_index.items() if bvid not in progress_cache]
        if self._cfg.debug_mode:
            pending = pending[:self._cfg.debug_item_limit]

        if not pending:
            logger.info("所有节点已提纯完毕")
            return

        logger.info("开始提纯 %d 个节点", len(pending))

        cookies = self._auth.get_cookies()
        headers = {"User-Agent": self._random_ua()}

        current_run_high = {}
        continuous = 0
        pause_threshold = random.randint(150, 200)

        try:
            async with httpx.AsyncClient(cookies=cookies, headers=headers, timeout=15.0) as client:
                for i, (bvid, node) in enumerate(pending, 1):
                    if i % 10 == 0:
                        logger.info("[%d/%d] 提纯: %s", i, len(pending), bvid)

                    enriched = await self._enrich_node(client, bvid, node)
                    if enriched:
                        progress_cache[bvid] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        if enriched["cognitive_impact_factor"] >= 1.0:
                            master_enriched[bvid] = enriched
                            current_run_high[bvid] = enriched
                        self._storage.save_progress(progress_cache, self._cfg.enrich_progress_file)
                        self._storage.safe_save_json(master_enriched, self._cfg.master_enriched_file)

                    # 防风控休眠
                    continuous += 1
                    if continuous >= pause_threshold:
                        await self._deep_sleep()
                        continuous = 0
                        pause_threshold = random.randint(150, 200)

        except asyncio.CancelledError:
            logger.warning("收到中断信号，进度已保存")
        except KeyboardInterrupt:
            logger.warning("手动终止，进度已保存")
        except Exception as e:
            logger.exception("未预期异常: %s", e)
        finally:
            if current_run_high:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                self._storage.safe_save_json(
                    current_run_high,
                    f"{self._cfg.enrich_dir}/enriched_links_{ts}.json",
                )
            logger.info("提纯完毕，高价值节点总计: %d", len(master_enriched))

    # ==================== node enrichment ====================

    async def _enrich_node(self, client: httpx.AsyncClient, bvid: str, node: dict) -> dict:
        author_mid = node["metadata"].get("author_mid", 0)
        headers = {**client.headers, "Referer": f"https://www.bilibili.com/video/{bvid}/"}

        # --- 请求1: 基础元数据 ---
        aid = await self._fetch_view_info(client, bvid, node, headers)
        await asyncio.sleep(random.uniform(1.0, 2.5))

        # --- 请求1.5: 标签 ---
        if aid:
            await self._fetch_tags(client, aid, node, headers)
        await asyncio.sleep(random.uniform(0.5, 1.2))

        # --- 请求2: 互动状态 ---
        await self._fetch_interaction(client, bvid, node, headers)
        await asyncio.sleep(random.uniform(1.2, 2.5))

        # --- 请求3: UP主关注 ---
        await self._fetch_relation(client, author_mid, node, headers)

        node["cognitive_impact_factor"] = self._recalc_cif(node)
        return node

    async def _fetch_view_info(self, client, bvid, node, headers) -> int | None:
        url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
        try:
            resp = await client.get(url, headers=headers, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 0:
                    v_data = data.get("data", {})
                    node["metadata"]["category"] = v_data.get("tname", "未知分类")
                    node["metadata"]["description"] = v_data.get("desc", "")
                    return v_data.get("aid")
        except Exception as e:
            logger.debug("元数据获取异常 %s: %s", bvid, e)
        return None

    async def _fetch_tags(self, client, aid, node, headers):
        url = f"https://api.bilibili.com/x/tag/archive/tags?aid={aid}"
        try:
            resp = await client.get(url, headers=headers, timeout=5.0)
            if resp.status_code == 200 and resp.json().get("code") == 0:
                tags = resp.json().get("data", [])
                node["metadata"]["tags"] = [t.get("tag_name") for t in tags if t.get("tag_name")]
        except Exception:
            node["metadata"]["tags"] = []

    async def _fetch_interaction(self, client, bvid, node, headers):
        url = f"https://api.bilibili.com/x/web-interface/archive/relation?bvid={bvid}"
        try:
            resp = await client.get(url, headers=headers, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 0:
                    d = data.get("data", {})
                    node["interaction_status"]["is_liked"] = bool(d.get("like", 0))
                    node["interaction_status"]["is_favorited"] = bool(d.get("favorite", 0))
                    node["interaction_status"]["coin_count"] = int(d.get("coin", 0))
        except Exception as e:
            logger.debug("互动状态异常 %s: %s", bvid, e)

    async def _fetch_relation(self, client, author_mid, node, headers):
        if not author_mid or author_mid == 0:
            node["interaction_status"]["is_followed"] = False
            return
        if author_mid in self._author_relation_cache:
            node["interaction_status"]["is_followed"] = self._author_relation_cache[author_mid]
            return

        url = f"https://api.bilibili.com/x/relation?fid={author_mid}"
        try:
            resp = await client.get(url, headers=headers, timeout=10.0)
            data = resp.json()
            if data.get("code") == 0:
                attr = data.get("data", {}).get("attribute", 0)
                self._author_relation_cache[author_mid] = (attr in [2, 6])
            else:
                self._author_relation_cache[author_mid] = False
        except Exception:
            self._author_relation_cache[author_mid] = False
        await asyncio.sleep(random.uniform(0.5, 1.2))
        node["interaction_status"]["is_followed"] = self._author_relation_cache[author_mid]

    # ==================== helpers ====================

    @staticmethod
    def _recalc_cif(node: dict) -> float:
        cif = node["cognitive_impact_factor"]
        s = node["interaction_status"]
        if s.get("is_liked"):
            cif += 1.0
        if s.get("is_followed"):
            cif += 2.0
        if s.get("is_favorited"):
            cif += 2.0
        coin = s.get("coin_count", 0)
        if coin > 0:
            cif += 1.5 * coin
        return round(cif, 3)

    async def _deep_sleep(self) -> None:
        minutes = random.uniform(10, 20)
        total_seconds = int(minutes * 60)
        logger.info("防风控深度休眠 %.1f 分钟...", minutes)
        for remaining in range(total_seconds, 0, -10):
            await asyncio.sleep(min(10, remaining))

    @staticmethod
    def _random_ua() -> str:
        return random.choice([
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
        ])

    def _has_input(self) -> bool:
        import os
        return os.path.exists(self._cfg.master_index_file)
