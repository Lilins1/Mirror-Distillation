"""Stage 4: 数据聚合器 — 将知识颗粒重组为 6 维 Markdown 研究报告"""

import os
import sys
import json
import math
import logging
from datetime import datetime
from collections import defaultdict

from .config import PipelineConfig
from .storage import DataStorage

logger = logging.getLogger(__name__)


class Stage4Aggregator:
    """加载 Stage3 知识颗粒，重算高级 CIF，生成 6 维研究报告"""

    def __init__(self, config: PipelineConfig):
        self._cfg = config
        self._storage = DataStorage()

    # ==================== public API ====================

    async def run(self) -> None:
        logger.info("启动数据变压器")
        all_nodes = self._load_and_score()
        if not all_nodes:
            logger.error("无有效 Stage 3 数据")
            return

        top_n = max(10, int(len(all_nodes) * self._cfg.top_percentile))
        high_value = all_nodes[:top_n]

        logger.info("加载 %d 个节点，截取前 %d (Top %.0f%%)",
                     len(all_nodes), top_n, self._cfg.top_percentile * 100)

        run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        reports = {
            "01-core-consumption.md": self._gen_01(high_value),
            "02-value-resonances.md": self._gen_02(high_value),
            "03-expression-dna.md": self._gen_03(high_value),
            "04-boundaries-rejections.md": self._gen_04(high_value),
            "05-decision-heuristics.md": self._gen_05(high_value),
            "06-timeline.md": self._gen_06(all_nodes),
        }

        for filename, content in reports.items():
            self._write_report(filename, content, run_ts)
            logger.info("已生成: %s", filename)

        logger.info("变压完成 → %s/", self._cfg.research_dir)

    # ==================== CIF ====================

    def _recalc_cif(self, node: dict) -> float:
        metadata = node.get("metadata", {})
        ai = node.get("ai_distillation", {})
        duration = max(metadata.get("duration", 1), 1)
        progress = metadata.get("progress", 0)
        completion = min(progress / duration, self._cfg.cif_completion_rate_max_cap)
        ks = ai.get("knowledge_value_score", 1)
        explicit = float(node.get("cognitive_impact_factor", 1.0))
        base = self._cfg.cif_base_behavior_weight * math.log(duration + 1) * completion
        multiplier = 0.5 + self._cfg.cif_knowledge_score_weight * ks
        return round(base * multiplier + explicit, 3)

    def _load_and_score(self) -> list:
        if not os.path.exists(self._cfg.stage3_dir):
            logger.error("Stage3 目录不存在: %s", self._cfg.stage3_dir)
            return []
        nodes = []
        for fn in os.listdir(self._cfg.stage3_dir):
            if not fn.endswith(".json"):
                continue
            try:
                data = self._storage.load_json(os.path.join(self._cfg.stage3_dir, fn))
                ai = data.get("ai_distillation", {})
                if ai.get("mode") != "failed" and not ai.get("is_ad_contaminated", False):
                    data["computed_cif"] = self._recalc_cif(data)
                    nodes.append(data)
            except Exception as e:
                logger.warning("解析失败 %s: %s", fn, e)
        nodes.sort(key=lambda x: x["computed_cif"], reverse=True)
        return nodes

    # ==================== report generators ====================

    def _gen_01(self, nodes: list) -> str:
        md = "# 01. 核心知识摄入域\n\n"
        md += f"> Top {self._cfg.top_percentile*100:.0f}% 高价值视频总结\n\n"
        cats = defaultdict(list)
        for n in nodes:
            cat = n["ai_distillation"].get("tags", {}).get("primary_category", "未分类")
            title = n["metadata"].get("title", "")
            summary = n["ai_distillation"].get("summary", "").replace("\n", " ")
            cif = n["computed_cif"]
            cats[cat].append(f"- **[BV:{n['video_id']} | CIF:{cif:.1f}] {title}**\n  *{summary}")
        for cat, items in cats.items():
            md += f"## {cat}\n" + "\n\n".join(items[:15]) + "\n\n"
        return md

    def _gen_02(self, nodes: list) -> str:
        md = "# 02. 价值共鸣点\n\n> 提取核心信念与价值观偏好\n\n"
        cnt = 0
        for n in nodes:
            p = n.get("ai_distillation", {}).get("cognitive_profile", {})
            beliefs = p.get("core_beliefs", "")
            values = p.get("values_preferences", "")
            if self._valid(beliefs) or self._valid(values):
                md += f"### BV:{n['video_id']} (CIF:{n['computed_cif']:.1f})\n"
                if self._valid(beliefs):
                    md += f"- 底层信念: {beliefs}\n"
                if self._valid(values):
                    md += f"- 价值偏好: {values}\n"
                md += "\n"
                cnt += 1
                if cnt >= 30:
                    break
        return md

    def _gen_03(self, nodes: list) -> str:
        md = "# 03. 表达DNA映射\n\n> 从沉浸内容中反推理想叙事节奏\n\n"
        cnt = 0
        for n in nodes:
            p = n.get("ai_distillation", {}).get("cognitive_profile", {})
            style = p.get("language_style", "")
            tone = p.get("emotional_tone", "")
            if self._valid(style) or self._valid(tone):
                md += f"- BV:{n['video_id']}: {style} | {tone}\n"
                cnt += 1
                if cnt >= 40:
                    break
        return md

    def _gen_04(self, nodes: list) -> str:
        md = "# 04. 边界推断证据\n\n> 本文件不直接给结论，仅整理可反推认知边界的证据\n\n"
        md += "## 数据限制\n- 无显式拉黑/不感兴趣记录\n- 排斥区只能镜像反推\n\n"
        md += "## 边界推断约束\n- 仅从重复出现的偏好反推\n- 跨域复现视为候选\n- 所有结论保留推断性质\n\n"

        sections = {"价值与信念证据": [], "表达偏好证据": [], "决策偏好证据": []}

        for n in nodes:
            p = n.get("ai_distillation", {}).get("cognitive_profile", {})
            bvid = n.get("video_id", "")
            cat = n.get("ai_distillation", {}).get("tags", {}).get("primary_category", "")
            cif = n.get("computed_cif", 0)

            beliefs = p.get("core_beliefs", "")
            values = p.get("values_preferences", "")
            if self._valid(beliefs) or self._valid(values):
                parts = []
                if self._valid(beliefs):
                    parts.append(f"信念: {self._compact(beliefs)}")
                if self._valid(values):
                    parts.append(f"价值: {self._compact(values)}")
                sections["价值与信念证据"].append(f"- BV:{bvid} | {cat} | CIF:{cif:.1f} | {' | '.join(parts)}")

            style = p.get("language_style", "")
            tone = p.get("emotional_tone", "")
            if self._valid(style) or self._valid(tone):
                parts = []
                if self._valid(style):
                    parts.append(f"风格: {self._compact(style)}")
                if self._valid(tone):
                    parts.append(f"情绪: {self._compact(tone)}")
                sections["表达偏好证据"].append(f"- BV:{bvid} | {cat} | CIF:{cif:.1f} | {' | '.join(parts)}")

            think = p.get("thinking_mode", "")
            dec = p.get("decision_pattern", "")
            if self._valid(think) or self._valid(dec):
                parts = []
                if self._valid(think):
                    parts.append(f"思维: {self._compact(think)}")
                if self._valid(dec):
                    parts.append(f"决策: {self._compact(dec)}")
                sections["决策偏好证据"].append(f"- BV:{bvid} | {cat} | CIF:{cif:.1f} | {' | '.join(parts)}")

        for name, items in sections.items():
            md += f"## {name}\n"
            md += ("\n".join(items[:8]) + "\n\n") if items else "- 暂无足够证据\n\n"

        md += "## 使用建议\n- 适合作为 Stage5 边界证据输入\n- 优先提取跨域稳定信号\n"
        return md

    def _gen_05(self, nodes: list) -> str:
        md = "# 05. 决策启发式证据\n\n> 思维模式与决策权重原则\n\n"
        cnt = 0
        for n in nodes:
            p = n.get("ai_distillation", {}).get("cognitive_profile", {})
            think = p.get("thinking_mode", "")
            dec = p.get("decision_pattern", "")
            fw = p.get("knowledge_framework", "")
            if self._valid(think) or self._valid(dec) or self._valid(fw):
                md += f"### BV:{n['video_id']} ({n.get('ai_distillation', {}).get('tags', {}).get('primary_category', '')})\n"
                if self._valid(think):
                    md += f"- 思维: {think}\n"
                if self._valid(fw):
                    md += f"- 框架: {fw}\n"
                if self._valid(dec):
                    md += f"- 决策: {dec}\n"
                md += "\n"
                cnt += 1
                if cnt >= 30:
                    break
        return md

    def _gen_06(self, nodes: list) -> str:
        valid = [n for n in nodes if n.get("metadata", {}).get("view_at")]
        valid.sort(key=lambda x: x["metadata"]["view_at"])

        timeline = defaultdict(list)
        for n in valid:
            vt = datetime.fromtimestamp(n["metadata"]["view_at"])
            timeline[vt.strftime("%Y年%m月")].append(n)

        months = sorted(timeline.keys())
        enough = len(months) >= 3
        title = "06. 认知演化线" if enough else "06. 近期认知切片"

        md = f"# {title}\n\n> 时间序列主题梳理\n\n## 数据充分性\n"
        md += f"- 时间片: {len(months)} | 节点: {len(valid)}\n"
        md += f"- 支持演化判断: {'是' if enough else '否'}\n\n"

        for month in months:
            mn = timeline[month]
            scores = defaultdict(float)
            counts = defaultdict(int)
            for n in mn:
                cat = n.get("ai_distillation", {}).get("tags", {}).get("primary_category", "未分类")
                scores[cat] += float(n.get("computed_cif", 0))
                counts[cat] += 1
            ranked = sorted(scores.items(), key=lambda x: (x[1], counts[x[0]]), reverse=True)
            top_nodes = sorted(mn, key=lambda n: n.get("computed_cif", 0), reverse=True)[:5]

            md += f"## {month}\n"
            if ranked:
                parts = [f"{c}(CIF={s:.1f}, n={counts[c]})" for c, s in ranked[:3]]
                md += f"- 高权重: {'；'.join(parts)}\n"
            for n in top_nodes:
                cat = n.get("ai_distillation", {}).get("tags", {}).get("primary_category", "")
                md += f"- [{cat}] {n.get('metadata', {}).get('title', '')}\n"
            md += "\n"

        if enough:
            md += "## 观察结论（谨慎）\n"
            md += f"- 起始: {months[0]}；末端: {months[-1]}\n"
        return md

    # ==================== helpers ====================

    @staticmethod
    def _valid(text) -> bool:
        if not text or not isinstance(text, str):
            return False
        low = text.lower()
        return "insufficient_data" not in low and "无法推断" not in low

    @staticmethod
    def _compact(text: str, max_len: int = 140) -> str:
        if not text or not isinstance(text, str):
            return ""
        text = " ".join(text.split())
        return text if len(text) <= max_len else text[:max_len - 3] + "..."

    def _write_report(self, filename: str, content: str, run_ts: str):
        # latest
        self._storage.write_text(os.path.join(self._cfg.research_dir, filename), content)
        # archive
        archive = os.path.join(self._cfg.history_dir, run_ts, "references", "research")
        self._storage.write_text(os.path.join(archive, filename), content)
