"""Stage 3: LLM 深度蒸馏器 — 对字幕进行结构化总结，提取知识颗粒与认知画像"""

import os
import math
import random
import time
import asyncio
import logging
from datetime import datetime
from typing import Optional

from .config import PipelineConfig, DeepSeekConfig
from .storage import DataStorage
from .llm_client import LLMClient

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

    async def run_batch_polling(self, videos: dict, subtitles_dir: str,
                                 output_dir: str, progress_file: str,
                                 done: asyncio.Event) -> None:
        """轮询模式 batch 总结：边等字幕产出边总结（供 UP Persona 并行模式使用）"""
        ds_config = DeepSeekConfig.from_file(self._cfg.deepseek_config_path)
        os.makedirs(output_dir, exist_ok=True)
        progress_cache = self._storage.load_progress(progress_file)
        last_processed = set(progress_cache.keys())

        while True:
            if self._quota_exhausted:
                logger.warning("余额耗尽，停止轮询")
                break

            pending = []
            for bvid, node in videos.items():
                if bvid in progress_cache or bvid in last_processed:
                    continue
                sub_path = os.path.join(subtitles_dir, f"{bvid}.json")
                if not os.path.exists(sub_path):
                    continue
                try:
                    sd = self._storage.load_json(sub_path)
                    if sd.get("full_text", "").strip():
                        pending.append((bvid, node))
                except Exception:
                    continue

            if pending:
                if self._cfg.debug_mode:
                    pending = pending[:self._cfg.debug_item_limit]
                logger.info("  轮询发现 %d 个新字幕，开始总结", len(pending))
                await self._process_batch(pending, progress_cache, ds_config,
                                            subtitles_dir=subtitles_dir, output_dir=output_dir,
                                            progress_file=progress_file)
                for bvid, _ in pending:
                    last_processed.add(bvid)
            elif done.is_set():
                logger.info("  字幕提取完成，轮询结束")
                break
            else:
                await asyncio.sleep(self._cfg.stage3_poll_interval)

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

            loaded = self._load_subtitle(bvid, node, subtitles_dir)
            if not loaded:
                await self._mark_skipped(bvid, progress_cache, completed, lock, total, start_time,
                                         progress_file=progress_file)
                return
            sub_data, full_text, metadata, impact, text_len = loaded

            # 分段 vs 单次
            use_segmented = self._cfg.enable_segmented_summary and text_len > self._cfg.segment_chunk_size
            if use_segmented:
                title = metadata.get("title", "")
                desc = metadata.get("description", "")
                tags = metadata.get("tags", [])
                ai_result, chosen_model = await self._segmented_summary(
                    ds_config, full_text, title, desc, tags, impact, text_len
                )
            else:
                chosen_model = self._cfg.model_small
                ai_result = await self._summarize_single(
                    ds_config, full_text, metadata.get("title", ""),
                    metadata.get("description", ""), metadata.get("tags", []),
                    text_len, impact, bvid,
                    int(metadata.get("duration", 0)),
                )
                if not ai_result:
                    ai_result = {"mode": "failed", "summary": "", "tags": {},
                                 "knowledge_value_score": 0, "is_ad_contaminated": False}

            self._save_grain(bvid, node, sub_data, metadata, ai_result, chosen_model,
                             progress_cache, output_dir, progress_file)

            async with lock:
                completed[0] += 1
                elapsed = time.time() - start_time
                avg = elapsed / completed[0]
                remaining = (total - completed[0]) * avg
                pct = (completed[0] / total) * 100
                logger.info("  [PROG] %d/%d (%.1f%%) | 预计剩余: %.1f分钟",
                            completed[0], total, pct, remaining / 60)

            await asyncio.sleep(random.uniform(5.0, 12.0))

    def _load_subtitle(self, bvid: str, node: dict, subtitles_dir: str = None):
        """加载并校验字幕文件。返回 (sub_data, full_text, metadata, impact, text_len) 或 None"""
        duration = int(node.get("metadata", {}).get("duration", 0) or 0)
        if duration < self._cfg.min_video_duration_seconds:
            return None
        _sub_dir = subtitles_dir or self._cfg.subtitles_dir
        sub_path = os.path.join(_sub_dir, f"{bvid}.json")
        if not os.path.exists(sub_path):
            return None
        sub_data = self._storage.load_json(sub_path)
        full_text = sub_data.get("full_text", "").strip()
        if not full_text:
            return None
        metadata = node.get("metadata", {})
        impact = float(node.get("cognitive_impact_factor", 5.0))
        text_len = len(full_text)
        return sub_data, full_text, metadata, impact, text_len

    def _save_grain(self, bvid: str, node: dict, sub_data: dict, metadata: dict,
                    ai_result: dict, chosen_model: str, progress_cache: dict,
                    output_dir: str = None, progress_file: str = None):
        """组装 grain 并写盘"""
        distillation = {
            "model": chosen_model,
            "timestamp": datetime.now().isoformat(),
            "mode": ai_result.get("mode", "unknown"),
            "summary": ai_result.get("summary", ""),
            "tags": ai_result.get("tags", {}),
            "knowledge_value_score": ai_result.get("knowledge_value_score", 0),
            "is_ad_contaminated": ai_result.get("is_ad_contaminated", False),
        }
        if ai_result.get("summary_long"):
            distillation["summary_long"] = ai_result["summary_long"]
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
        self._storage.safe_save_json(grain, os.path.join(_out_dir, f"{bvid}.json"))
        progress_cache[bvid] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._storage.save_progress(progress_cache, _prog_file)

    # ==================== single / dual summary ====================

    async def _summarize_single(self, ds_config: DeepSeekConfig, full_text: str, title: str,
                                 desc: str, tags: list, text_len: int, impact: float,
                                 bvid: str, duration: int) -> Optional[dict]:
        """根据 CIF 分数决定精简摘要或双摘要（短+长并行）。

        模型策略：基本总结（低要求）统一走 flash；深度画像（高要求）才用 pro。
        """
        score_metric = self._calc_score(impact, text_len)
        dual_threshold = self._cfg.dual_summary_threshold if self._cfg.dual_summary_threshold > 0 else self._cfg.cognitive_value_threshold
        enable_dual = score_metric >= dual_threshold and text_len > 300

        short_model = self._cfg.model_small
        long_model = self._cfg.model_large

        if enable_dual:
            sp_s, up_s, tok_s = self._build_short_prompt(full_text, title, desc, tags, text_len, impact)
            sp_l, up_l, tok_l = self._build_long_prompt(full_text, title, desc, tags, text_len, impact)
            logger.info("处理: %s | %s... (时长:%ds, 字数:%d, CIF:%.1f, 双摘要模式: %s + %s)",
                        bvid, title[:20], duration, text_len, impact, short_model, long_model)
            short_task = self._call_deepseek(ds_config, short_model, sp_s, up_s, tok_s)
            long_task = self._call_deepseek(ds_config, long_model, sp_l, up_l, tok_l, temperature=0.35)
            short_r, long_r = await asyncio.gather(short_task, long_task)
            if not short_r:
                short_r = {"summary": "", "tags": {}, "knowledge_value_score": 0, "is_ad_contaminated": False}
            result = {
                "mode": "dual_summary",
                "summary": short_r.get("summary", ""),
                "tags": short_r.get("tags", {}),
                "knowledge_value_score": short_r.get("knowledge_value_score", 0),
                "is_ad_contaminated": short_r.get("is_ad_contaminated", False),
            }
            if long_r:
                result["summary_long"] = long_r.get("summary_long", "")
                result["cognitive_signal_strength"] = long_r.get("cognitive_signal_strength", 0)
                result["cognitive_profile"] = long_r.get("cognitive_profile", {})
                result["knowledge_value_score"] = max(result["knowledge_value_score"],
                                                      long_r.get("knowledge_value_score", 0))
                result["is_ad_contaminated"] = result["is_ad_contaminated"] or long_r.get("is_ad_contaminated", False)
                if long_r.get("tags"):
                    result["tags"] = long_r["tags"]
            return result
        else:
            sp_s, up_s, tok_s = self._build_short_prompt(full_text, title, desc, tags, text_len, impact)
            logger.info("处理: %s | %s... (时长:%ds, 字数:%d, CIF:%.1f, 模型:%s)",
                        bvid, title[:20], duration, text_len, impact, short_model)
            result = await self._call_deepseek(ds_config, short_model, sp_s, up_s, tok_s)
            if result:
                result["mode"] = "short_summary" if text_len <= 1500 else ("medium_summary" if text_len <= 8000 else "deep_structured")
            return result

    # ==================== segmented summary ====================

    async def _segmented_summary(self, ds_config: DeepSeekConfig, full_text: str,
                                  title: str, desc: str, tags: list, impact: float,
                                  text_len: int) -> tuple:
        score_metric = self._calc_score(impact, text_len)
        logger.info("超长视频: %s... (字数:%d, CIF:%.1f, score:%.1f)", title[:20], text_len, impact, score_metric)

        segments = self._split_text(full_text, self._cfg.segment_chunk_size)
        logger.info("分为 %d 段", len(segments))

        seg_summaries = []
        for i, seg in enumerate(segments):
            sp = "你是知识摘要专家。提炼本段字幕的：1)核心论点 2)关键证据/案例 3)重要数据。中文输出，拒绝空泛。"
            up = f"字幕片段 {i+1}/{len(segments)}：\n---\n{seg}\n---\n严格按JSON输出：{{\"summary\": \"150-300字精炼摘要\"}}"
            seg_max_tok = min(1500, max(400, int(len(seg) * 0.5)))
            result = await self._call_deepseek(
                ds_config, self._cfg.model_small,
                sp, up, seg_max_tok, temperature=0.3, retry=2,
            )
            seg_summaries.append(result.get("summary", "[失败]") if result else "[失败]")
            await asyncio.sleep(random.uniform(2.0, 5.0))

        merged = "\n\n---\n\n".join(seg_summaries)
        chosen_model = self._cfg.model_small

        # 对合并后的分段摘要应用双摘要逻辑
        ai_result = await self._summarize_single(
            ds_config, merged, title, desc, tags, len(merged), impact,
            f"{title[:10]}...(分段)", text_len
        )
        if not ai_result:
            ai_result = {"mode": "segmented_fallback", "summary": merged, "tags": {}, "knowledge_value_score": 0, "is_ad_contaminated": False}
        return ai_result, chosen_model

    # ==================== prompt builders ====================

    def _build_short_prompt(self, text: str, title: str, desc: str, tags: list,
                            text_len: int, impact: float = 5.0) -> tuple:
        """构建精炼摘要 prompt — 提取核心论点、关键证据、实践价值，不提取认知画像"""
        truncated = text[:self._cfg.max_input_chars]
        if len(text) > self._cfg.max_input_chars:
            truncated += f"\n[注意：原字幕{len(text)}字，已截取前{self._cfg.max_input_chars}字]"

        max_tok = 2000 if text_len <= 8000 else 3000

        sp = """你是知识蒸馏专家。你的任务是将视频字幕提炼为高质量的精炼摘要。

严格遵循：
1. 提取视频的 3-5 个核心论点或关键信息点
2. 指出支撑论点的关键证据或案例
3. 提炼可迁移的实践知识或思维方式
4. 过滤广告、口播、求赞等非内容部分
5. 中文输出，信息密度高，拒绝空泛概括"""

        up = f"""视频标题：{title}
视频简介：{desc}
标签：{', '.join(tags) if tags else '无'}
视频内容文本：
---
{truncated}
---
请严格按以下JSON格式输出：

{{
  "summary": "200-400字精炼摘要。以'本视频讨论了...'开头，涵盖核心论点、关键证据和主要结论。",
  "tags": {{"primary_category": "一级分类", "secondary_category": "二级分类", "keywords": ["关键词1", "关键词2", ...]}},
  "knowledge_value_score": 1-10,
  "is_ad_contaminated": true/false
}}

注意：
- summary 必须严格基于字幕内容，不编造未出现的信息
- knowledge_value_score: 1=纯娱乐/闲聊, 10=高度结构化知识/可迁移方法论
- is_ad_contaminated: 字幕中是否含有大量广告/带货/求三连等内容"""
        return sp, up, max_tok

    def _build_long_prompt(self, text: str, title: str, desc: str, tags: list,
                           text_len: int, impact: float = 5.0) -> tuple:
        """构建深度分析 prompt — 结构化论证分析 + 认知画像提取"""
        truncated = text[:self._cfg.max_input_chars]
        if len(text) > self._cfg.max_input_chars:
            truncated += f"\n[注意：原字幕{len(text)}字，已截取前{self._cfg.max_input_chars}字]"

        sp = """你是认知分析专家。你的任务是对视频字幕进行深度结构化分析。

核心原则：
1. 严格基于原文证据，绝不编造或推测未出现的内容
2. 当证据不足时，相应字段填写 "insufficient_data"
3. 区分"作者陈述的观点"与"可客观验证的事实"
4. 关注论证中的隐藏前提、逻辑跳跃和未讨论的反方视角
5. 中文输出，分析要有穿透力而非堆砌描述"""

        up = f"""视频标题：{title}
视频简介：{desc}
标签：{', '.join(tags) if tags else '无'}
视频内容文本：
---
{truncated}
---
请严格按以下JSON格式输出：

{{
  "summary_long": "600-1200字深度分析。按以下结构组织：
  【核心论点】视频最想传达的1-3个核心主张
  【论证结构】作者如何构建论证？使用了什么类型的证据（数据/案例/逻辑推演/权威引用）？
  【关键洞察】视频中真正有价值、可迁移的见解是什么？与常见认知有何不同？
  【实践价值】这些内容如何应用到实际工作或思考中？
  【局限与盲区】视频未讨论但相关的维度、可能的反方论点、论证中的跳跃",

  "tags": {{"primary_category": "一级分类", "secondary_category": "二级分类", "keywords": ["关键词1", ...]}},
  "knowledge_value_score": 1-10,
  "is_ad_contaminated": true/false,
  "cognitive_signal_strength": 1-10,
  "cognitive_profile": {{
    "language_style": "表达风格：术语密度、句式偏好、修辞习惯等。不足填'insufficient_data'",
    "thinking_mode": "思维模式：归纳/演绎/类比/系统思维/批判性等。不足填'insufficient_data'",
    "values_preferences": "价值偏好：效率/公平/创新/传统/实用主义等倾向。不足填'insufficient_data'",
    "core_beliefs": "核心信念：反复出现或隐含不言的底层预设。不足填'insufficient_data'",
    "argumentation_pattern": "论证模式：数据驱动/案例叙事/权威引用/逻辑链条等特征。不足填'insufficient_data'",
    "emotional_tone": "情感基调：理性克制/激情澎湃/冷嘲/温暖/焦虑等。不足填'insufficient_data'",
    "knowledge_framework": "知识框架：作者调用什么领域的知识体系来解释问题。不足填'insufficient_data'",
    "decision_pattern": "决策逻辑：呈现出的'如何做选择'的默认策略和优先级。不足填'insufficient_data'"
  }}
}}

关键提醒：
- cognitive_profile 中任何没有充分证据的维度，务必填 "insufficient_data"，宁缺毋滥
- summary_long 必须有实质分析，不能是字幕内容的简单复述
- cognitive_signal_strength: 评估视频中认知特征的显露程度，1=几乎看不到思维痕迹, 10=认知风格非常鲜明"""
        return sp, up, 6000

    # ==================== LLM & helpers ====================

    async def _call_deepseek(self, ds_config: DeepSeekConfig, model: str,
                             system: str, user: str, max_tokens: int,
                             temperature: float = 0.3, retry: int = 3) -> Optional[dict]:
        client = LLMClient(ds_config)
        result = await client.chat_json(model, system, user, max_tokens, temperature, retry)
        if client.quota_exhausted:
            self._quota_exhausted = True
        return result

    @staticmethod
    def _calc_score(impact: float, text_len: int) -> float:
        return impact * math.log(text_len + 1) if text_len > 0 else 0.0

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
