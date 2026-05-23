"""Stage 5.1: 研究报告合并 — 汇总6维报告为检查摘要表格"""

import re
import logging
from pathlib import Path

from .config import PipelineConfig
from .storage import DataStorage

logger = logging.getLogger(__name__)

AGENTS = {
    "01-core-consumption": "核心知识域",
    "02-value-resonances": "价值共鸣点",
    "03-expression-dna": "表达DNA",
    "04-boundaries-rejections": "诚实边界",
    "05-decision-heuristics": "推演决策树",
    "06-timeline": "认知演化线",
}


class ResearchMerger:
    """汇总 Stage4 的 6 个 MD 文件为检查表格"""

    def __init__(self, config: PipelineConfig):
        self._cfg = config

    def run(self):
        import os
        research_dir = self._cfg.research_dir
        if not os.path.exists(research_dir):
            logger.error("目录不存在: %s", research_dir)
            return

        files = {}
        rows = []
        total_sources = 0
        missing = []

        for key, label in AGENTS.items():
            md_path = os.path.join(research_dir, f"{key}.md")
            if not os.path.exists(md_path):
                missing.append(label)
                rows.append(f"│ {label:<12} │ {'❌ 缺失':<8} │ {'—':<24} │")
                continue

            content = DataStorage.read_text(md_path)
            files[key] = content
            stats = self._count_sources(content)
            findings = self._extract_findings(content)
            total_sources += stats["unique_urls"]

            f_str = ", ".join(findings) if findings else "—"
            if len(f_str) > 40:
                f_str = f_str[:37] + "..."
            rows.append(f"│ {label:<12} │ {stats['unique_urls']:<8} │ {f_str:<24} │")

        contradictions = self._find_contradictions(files)

        self._print_table(rows, total_sources, contradictions, missing)

        if total_sources < 5:
            logger.warning("BV 引用数极少，建议检查 Stage3 产出")
        if missing:
            logger.warning("缺失维度: %s", ", ".join(missing))

    # ==================== helpers ====================

    @staticmethod
    def _count_sources(content: str) -> dict:
        urls = re.findall(r"BV[1-9A-HJ-NP-Za-km-z]{10}", content)
        return {"url_count": len(urls), "unique_urls": len(set(urls))}

    @staticmethod
    def _extract_findings(content: str, max_items: int = 3) -> list:
        headings = re.findall(r"^##\s+(.+)$", content, re.MULTILINE)
        if headings:
            return headings[:max_items]
        bolds = re.findall(r"\*\*(.+?)\*\*", content)
        if bolds:
            return bolds[:max_items]
        lines = [l.strip() for l in content.split("\n") if l.strip() and not l.startswith("#")]
        return [l[:50] + "..." if len(l) > 50 else l for l in lines[:max_items]]

    @staticmethod
    def _find_contradictions(files: dict) -> list:
        result = []
        for name, content in files.items():
            matches = re.findall(r"(?:矛盾|相反|但实际上|然而.*?不同|争议).{0,100}", content)
            for m in matches:
                result.append(f"{AGENTS.get(name, name)}: {m[:80]}")
        return result[:5]

    @staticmethod
    def _print_table(rows: list, total_sources: int, contradictions: list, missing: list):
        print("┌──────────────┬──────────┬──────────────────────────┐")
        print("│ Agent 维度   │ BV来源数 │ 关键发现                  │")
        print("├──────────────┼──────────┼──────────────────────────┤")
        for row in rows:
            print(row)
        print("├──────────────┼──────────┼──────────────────────────┤")
        print(f"│ 总 BV 引用数 │ {total_sources:<8} │ {'—':<24} │")
        if contradictions:
            print(f"│ 矛盾点        │ {len(contradictions)}处      │ {contradictions[0][:24]:<24} │")
        else:
            print(f"│ 矛盾点        │ 0处      │ {'—':<24} │")
        miss_str = ", ".join(missing) if missing else "无"
        print(f"│ 信息不足维度   │ {len(missing)}个      │ {miss_str:<24} │")
        print("└──────────────┴──────────┴──────────────────────────┘")
