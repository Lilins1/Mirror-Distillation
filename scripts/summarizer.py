"""Stage 3: LLM 深度蒸馏器 — 对字幕进行结构化总结，提取知识颗粒与认知画像"""

import os
import json
import math
import random
import time
import asyncio
import logging
from datetime import datetime
from typing import Optional

from openai import AsyncOpenAI

from .config import PipelineConfig, DeepSeekConfig
from .storage import DataStorage

logger = logging.getLogger(__name__)


class Stage3Summarizer:
    """使用 DeepSeek API 对视频字幕进行知识蒸馏"""

    def __init__(self, config: PipelineConfig):
        self._cfg = config
        self._storage = DataStorage()
        self._quota_exhausted = False

    # ==================== public API ====================

    async def run(self) -> None:
        ds_config = DeepSeekConfig.from_file(self._cfg.deepseek_config_path)
        await self._summarize_all(ds_config)

    async def run_parallel(self, done_event: Optional[asyncio.Event] = None) -> None:
        """轮询模式：持续等待 Stage2 产出新字幕并总结"""
        ds_config = DeepSeekConfig.from_file(self._cfg.deepseek_config_path)
        progress_cache = self._storage.load_progress(self._cfg.stage3_progress_file)
        last_processed = set(progress_cache.keys())
        parallel_start = time.time()

        while True:
            if self._quota_exhausted:
                logger.warning("余额耗尽，停止轮询")
                break

            pending = self._find_pending(progress_cache, last_processed)
            if self._cfg.debug_mode:
                pending = pending[:self._cfg.debug_item_limit]

            if pending:
                logger.info("发现 %d 个新字幕，开始批量总结", len(pending))
                await self._process_batch(pending, progress_cache, ds_config)
                for bvid, _ in pending:
                    last_processed.add(bvid)
            else:
                if done_event and done_event.is_set():
                    logger.info("生产者已停止，轮询结束")
                    break
                logger.debug("暂无新字幕，%d秒后重试", self._cfg.stage3_poll_interval)
                await asyncio.sleep(self._cfg.stage3_poll_interval)

        logger.info("并行模式退出，总用时 %.1f 分钟", (time.time() - parallel_start) / 60)

    async def run_batch(self, videos: dict, subtitles_dir: str,
                         output_dir: str, progress_file: str) -> dict:
        """处理自定义视频集合（供 UP Persona 复用）。
        返回 progress_cache。
        """
        ds_config = DeepSeekConfig.from_file(self._cfg.deepseek_config_path)
        os.makedirs(output_dir, exist_ok=True)
        progress_cache = self._storage.load_progress(progress_file)

        pending = []
        for bvid, node in videos.items():
            if bvid in progress_cache:
                continue
            sub_path = os.path.join(subtitles_dir, f"{bvid}.json")
            if not os.path.exists(sub_path):
                continue
            try:
                sd = self._storage.load_json(sub_path)
                if not sd.get("full_text", "").strip():
                    continue
            except Exception:
                continue
            pending.append((bvid, node))

        if self._cfg.debug_mode:
            pending = pending[:self._cfg.debug_item_limit]
        if not pending:
            logger.info("UP batch: 所有视频已总结")
            return progress_cache

        logger.info("UP batch: 总结 %d 个视频 → %s", len(pending), output_dir)
        await self._process_batch(pending, progress_cache, ds_config,
                                   subtitles_dir=subtitles_dir, output_dir=output_dir,
                                   progress_file=progress_file)
        return progress_cache

    # ==================== batch processing ====================

    async def _summarize_all(self, ds_config: DeepSeekConfig) -> None:
        progress_cache = self._storage.load_progress(self._cfg.stage3_progress_file)
        pending = self._find_pending(progress_cache, set())
        if self._cfg.debug_mode:
            pending = pending[:self._cfg.debug_item_limit]
        if not pending:
            logger.info("所有视频总结已完成")
            return

        logger.info("将处理 %d 个视频 (时长≥%ds)", len(pending), self._cfg.min_video_duration_seconds)
        await self._process_batch(pending, progress_cache, ds_config)

    async def _process_batch(self, items: list, progress_cache: dict, ds_config: DeepSeekConfig,
                              subtitles_dir: str = None, output_dir: str = None,
                              progress_file: str = None):
        sem = asyncio.Semaphore(self._cfg.stage3_concurrency_limit)
        start_time = time.time()
        completed = [0]
        lock = asyncio.Lock()

        tasks = [
            self._process_one(bvid, node, progress_cache, sem, start_time, completed, lock,
                              len(items), ds_config,
                              subtitles_dir=subtitles_dir, output_dir=output_dir,
                              progress_file=progress_file)
            for bvid, node in items
        ]
        await asyncio.gather(*tasks)
        logger.info("批次完成，总用时 %.1f 分钟", (time.time() - start_time) / 60)

    # ==================== single video processing ====================

    async def _process_one(self, bvid: str, node: dict, progress_cache: dict,
                           sem: asyncio.Semaphore, start_time: float, completed: list,
                           lock: asyncio.Lock, total: int, ds_config: DeepSeekConfig,
                           subtitles_dir: str = None, output_dir: str = None,
                           progress_file: str = None):
        async with sem:
            if bvid in progress_cache:
                return

            # 时长过滤
            duration = int(node.get("metadata", {}).get("duration", 0) or 0)
            if duration < self._cfg.min_video_duration_seconds:
                await self._mark_skipped(bvid, progress_cache, completed, lock, total, start_time, progress_file=progress_file)
                return

            _sub_dir = subtitles_dir or self._cfg.subtitles_dir
            sub_path = os.path.join(_sub_dir, f"{bvid}.json")
            if not os.path.exists(sub_path):
                await self._mark_skipped(bvid, progress_cache, completed, lock, total, start_time, progress_file=progress_file)
                return

            sub_data = self._storage.load_json(sub_path)
            full_text = sub_data.get("full_text", "").strip()
            if not full_text:
                await self._mark_skipped(bvid, progress_cache, completed, lock, total, start_time, progress_file=progress_file)
                return

            # 元数据
            metadata = node.get("metadata", {})
            title = metadata.get("title", "")
            desc = metadata.get("description", "")
            tags = metadata.get("tags", [])
            impact = float(node.get("cognitive_impact_factor", 5.0))
            text_len = len(full_text)

            # 计算 score_metric
            score_metric = impact * math.log(text_len + 1) if text_len > 0 else 0

            # 分段 vs 单次
            use_segmented = self._cfg.enable_segmented_summary and text_len > self._cfg.segment_chunk_size

            if use_segmented:
                ai_result, chosen_model = await self._segmented_summary(
                    ds_config, full_text, title, desc, tags, impact, text_len
                )
            else:
                chosen_model = self._select_model(impact, text_len)
                logger.info("处理: %s | %s... (时长:%ds, 字数:%d, CIF:%.1f, 模型:%s)",
                            bvid, title[:20], duration, text_len, impact, chosen_model)
                sp, up, max_tok = self._build_prompt(full_text, title, desc, tags, text_len, chosen_model, impact)
                ai_result = await self._call_deepseek(ds_config, chosen_model, sp, up, max_tok)
                if not ai_result:
                    ai_result = {"mode": "failed", "summary": "", "tags": {}, "knowledge_value_score": 0, "is_ad_contaminated": False}

            # 组装输出
            distillation = {
                "model": chosen_model,
                "timestamp": datetime.now().isoformat(),
                "mode": ai_result.get("mode", "unknown"),
                "summary": ai_result.get("summary", ""),
                "tags": ai_result.get("tags", {}),
                "knowledge_value_score": ai_result.get("knowledge_value_score", 0),
                "is_ad_contaminated": ai_result.get("is_ad_contaminated", False),
            }
            if "cognitive_signal_strength" in ai_result:
                distillation["cognitive_signal_strength"] = ai_result["cognitive_signal_strength"]
            if "cognitive_profile" in ai_result:
                distillation["cognitive_profile"] = ai_result["cognitive_profile"]

            grain = {
                "video_id": bvid,
                "metadata": metadata,
                "cognitive_impact_factor": node.get("cognitive_impact_factor", 0),
                "sponsor_block_info": sub_data.get("processing_status", {}).get("sponsor_block", {}),
                "ai_distillation": distillation,
            }

            _out_dir = output_dir or self._cfg.stage3_dir
            _prog_file = progress_file or self._cfg.stage3_progress_file
            out_path = os.path.join(_out_dir, f"{bvid}.json")
            self._storage.safe_save_json(grain, out_path)
            progress_cache[bvid] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._storage.save_progress(progress_cache, _prog_file)

            async with lock:
                completed[0] += 1
                elapsed = time.time() - start_time
                avg = elapsed / completed[0]
                remaining = (total - completed[0]) * avg
                pct = (completed[0] / total) * 100
                logger.info("  [PROG] %d/%d (%.1f%%) | 预计剩余: %.1f分钟", completed[0], total, pct, remaining / 60)

            await asyncio.sleep(random.uniform(5.0, 12.0))

    # ==================== segmented summary ====================

    async def _segmented_summary(self, ds_config: DeepSeekConfig, full_text: str,
                                  title: str, desc: str, tags: list, impact: float,
                                  text_len: int) -> tuple:
        score_metric = impact * math.log(text_len + 1)
        logger.info("超长视频: %s... (字数:%d, CIF:%.1f, score:%.1f)", title[:20], text_len, impact, score_metric)

        segments = self._split_text(full_text, self._cfg.segment_chunk_size)
        logger.info("分为 %d 段", len(segments))

        seg_summaries = []
        for i, seg in enumerate(segments):
            seg_user = f"字幕片段：\n---\n{seg}\n---\n请严格按JSON格式输出：\n{{\"summary\": \"100-300字摘要\"}}"
            seg_max_tok = min(1500, max(400, int(len(seg) * 0.5)))
            result = await self._call_deepseek(
                ds_config, self._cfg.model_small,
                "你是知识摘要专家。用中文总结字幕片段核心内容，保留关键信息。",
                seg_user, seg_max_tok, temperature=0.3, retry=2,
            )
            seg_summaries.append(result.get("summary", "[失败]") if result else "[失败]")
            await asyncio.sleep(random.uniform(2.0, 5.0))

        merged = "\n\n---\n\n".join(seg_summaries)
        final_model = self._select_model(impact, len(merged))
        enable_cog = (impact * math.log(text_len + 1)) >= self._cfg.cognitive_value_threshold

        final_user = self._build_segmented_user_prompt(title, desc, tags, merged, enable_cog)
        result = await self._call_deepseek(ds_config, final_model,
                                           self._build_segmented_system_prompt(enable_cog),
                                           final_user, 5000, temperature=0.3)
        if not result:
            result = {"mode": "segmented_fallback", "summary": merged, "tags": {}, "knowledge_value_score": 0, "is_ad_contaminated": False}
        return result, final_model

    # ==================== prompt builders ====================

    def _build_prompt(self, text: str, title: str, desc: str, tags: list,
                      text_len: int, chosen_model: str, impact: float = 5.0) -> tuple:
        score_metric = impact * math.log(text_len + 1) if text_len > 0 else 0
        enable_cog = score_metric >= self._cfg.cognitive_value_threshold

        if text_len <= 1500:
            mode, length_guide, max_tok = "short_summary", "100-200字", 800
        elif text_len <= 8000:
            mode, length_guide, max_tok = "medium_summary", "300-500字", 2000
        else:
            mode, length_guide, max_tok = "deep_structured", "不少于800字，结构化格式", 5000

        truncated = text[:self._cfg.max_input_chars]
        if len(text) > self._cfg.max_input_chars:
            truncated += f"\n[注意：原字幕{len(text)}字，已截取前{self._cfg.max_input_chars}字]"

        sp = "你是顶级知识蒸馏专家。严格基于字幕：1.提炼知识内容 2."
        if enable_cog:
            sp += "提取认知特征（仅原文证据，不足填'insufficient_data'）3."
        else:
            sp += "无需提取认知特征 3."
        sp += "过滤广告 4.中文输出。"

        up = f"""视频标题：{title}
视频简介：{desc}
标签：{', '.join(tags) if tags else '无'}
字幕：
---
{truncated}
---
严格按JSON输出：
{{
  "mode": "{mode}",
  "summary": "{length_guide}",
  "tags": {{"primary_category": "...", "secondary_category": "...", "keywords": [...]}},
  "knowledge_value_score": 1-10,
  "is_ad_contaminated": true/false"""

        if enable_cog:
            up += """,
  "cognitive_signal_strength": 1-10,
  "cognitive_profile": {
    "language_style": "...或insufficient_data",
    "thinking_mode": "...或insufficient_data",
    "values_preferences": "...或insufficient_data",
    "core_beliefs": "...或insufficient_data",
    "argumentation_pattern": "...或insufficient_data",
    "emotional_tone": "...或insufficient_data",
    "knowledge_framework": "...或insufficient_data",
    "decision_pattern": "...或insufficient_data"
  }"""
            max_tok = max(max_tok, 3000)

        up += "\n}"
        return sp, up, max_tok

    @staticmethod
    def _build_segmented_system_prompt(enable_cog: bool) -> str:
        sp = "你是顶级知识蒸馏专家。基于分段摘要整合成完整总结。"
        if enable_cog:
            sp += "若摘要反映思维特征，提取认知画像。必须客观精准，不足填'insufficient_data'。"
        else:
            sp += "信号弱，无需提取认知。"
        return sp

    @staticmethod
    def _build_segmented_user_prompt(title: str, desc: str, tags: list, merged: str, enable_cog: bool) -> str:
        cog_part = ""
        if enable_cog:
            cog_part = """,
  "cognitive_signal_strength": 1-10,
  "cognitive_profile": {
    "language_style": "...", "thinking_mode": "...", "values_preferences": "...",
    "core_beliefs": "...", "argumentation_pattern": "...", "emotional_tone": "...",
    "knowledge_framework": "...", "decision_pattern": "..."
  }"""
        return f"""视频标题：{title}
视频简介：{desc}
标签：{', '.join(tags) if tags else '无'}
分段摘要：
---
{merged}
---
严格按JSON输出：
{{
  "mode": "segmented_deep_structured",
  "summary": "综合结构化总结，不少于500字",
  "tags": {{"primary_category": "...", "secondary_category": "...", "keywords": [...]}},
  "knowledge_value_score": 1-10,
  "is_ad_contaminated": true/false{cog_part}
}}"""

    # ==================== LLM & helpers ====================

    async def _call_deepseek(self, ds_config: DeepSeekConfig, model: str,
                             system: str, user: str, max_tokens: int,
                             temperature: float = 0.3, retry: int = 3) -> Optional[dict]:
        client = AsyncOpenAI(api_key=ds_config.api_key, base_url=ds_config.base_url)
        for attempt in range(retry):
            try:
                response = await client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                )
                return json.loads(response.choices[0].message.content)
            except json.JSONDecodeError as e:
                logger.warning("JSON解析失败 (attempt %d/%d): %s", attempt + 1, retry, e)
                await asyncio.sleep(2 * (attempt + 1))
            except Exception as e:
                err = str(e).lower()
                if "402" in str(e) or "insufficient balance" in err or "quota" in err:
                    logger.error("余额不足: %s", e)
                    self._quota_exhausted = True
                    return None
                logger.warning("API异常: %s", e)
                if "rate" in err or "429" in err:
                    await asyncio.sleep(10 * (attempt + 1))
                elif "context" in err or "length" in err:
                    return None
                else:
                    await asyncio.sleep(3 * (attempt + 1))
        return None

    def _select_model(self, impact: float, text_len: int) -> str:
        score = impact * math.log(text_len + 1) if text_len > 0 else 0
        return self._cfg.model_large if score > self._cfg.high_value_threshold else self._cfg.model_small

    @staticmethod
    def _split_text(text: str, max_chars: int) -> list:
        paragraphs = text.split("\n")
        chunks = []
        current = ""
        for para in paragraphs:
            if len(current) + len(para) + 1 <= max_chars:
                current = f"{current}\n{para}" if current else para
            else:
                if current:
                    chunks.append(current)
                if len(para) > max_chars:
                    for i in range(0, len(para), max_chars):
                        chunks.append(para[i:i + max_chars])
                    current = ""
                else:
                    current = para
        if current:
            chunks.append(current)
        return chunks

    def _find_pending(self, progress_cache: dict, last_processed: set) -> list:
        master_path = self._cfg.master_enriched_file
        if not os.path.exists(master_path):
            return []
        master = self._storage.load_json(master_path)
        pending = []
        for bvid, node in master.items():
            if bvid in progress_cache or bvid in last_processed:
                continue
            sub_path = os.path.join(self._cfg.subtitles_dir, f"{bvid}.json")
            if not os.path.exists(sub_path):
                continue
            try:
                sd = self._storage.load_json(sub_path)
                if not sd.get("full_text", "").strip():
                    continue
            except Exception:
                continue
            pending.append((bvid, node))
        return pending

    async def _mark_skipped(self, bvid: str, progress_cache: dict, completed: list,
                            lock: asyncio.Lock, total: int, start_time: float,
                            progress_file: str = None):
        progress_cache[bvid] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._storage.save_progress(progress_cache, progress_file or self._cfg.stage3_progress_file)
        async with lock:
            completed[0] += 1
