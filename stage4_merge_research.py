#!/usr/bin/env python3
"""
合并6个维度的认知调研结果，生成阶段性调研 Review 检查点的摘要表格。
扫描 references/research/ 目录下的 01-06 md 文件，统计每个维度的视频来源数量 (BV号)、关键发现。

用法:
    python3 merge_research.py <skill目录路径>

示例:
    python3 merge_research.py data/stage4_persona_builder

输出: 打印 markdown 格式的摘要表格到 stdout
"""

import sys
import re
from pathlib import Path

# 适配 Bilibili 认知镜像的 6 个新维度
AGENTS = {
    '01-core-consumption': '核心知识域',
    '02-value-resonances': '价值共鸣点',
    '03-expression-dna': '表达DNA',
    '04-boundaries-rejections': '诚实边界',
    '05-decision-heuristics': '推演决策树',
    '06-timeline': '认知演化线',
}

def count_sources(content: str) -> dict:
    """统计来源数量（基于 Bilibili BV 号匹配）"""
    # 匹配 Bilibili 的 BV 号
    urls = re.findall(r'BV[1-9A-HJ-NP-Za-km-z]{10}', content)

    return {
        'url_count': len(urls),
        'unique_urls': len(set(urls)),
    }


def extract_key_findings(content: str, max_items: int = 3) -> list[str]:
    """提取关键发现（取前几个二级标题或加粗项）"""
    # 尝试提取##标题
    headings = re.findall(r'^##\s+(.+)$', content, re.MULTILINE)
    if headings:
        return headings[:max_items]

    # fallback: 提取加粗项
    bolds = re.findall(r'\*\*(.+?)\*\*', content)
    if bolds:
        return bolds[:max_items]

    # fallback: 取前3个非空行
    lines = [l.strip() for l in content.split('\n') if l.strip() and not l.startswith('#')]
    return [l[:50] + '...' if len(l) > 50 else l for l in lines[:max_items]]


def find_contradictions(files: dict[str, str]) -> list[str]:
    """简单检测跨文件矛盾（同一关键词出现不同判断）"""
    contradictions = []
    # 检测「但是」「然而」「相反」「矛盾」等矛盾标记
    for name, content in files.items():
        matches = re.findall(r'(?:矛盾|相反|但实际上|然而.*?不同|争议).{0,100}', content)
        for m in matches:
            contradictions.append(f"{AGENTS.get(name, name)}: {m[:80]}")
    return contradictions[:5]  # 最多5条


def main():
    if len(sys.argv) < 2:
        print("用法: python3 merge_research.py <skill目录路径>")
        sys.exit(1)

    skill_dir = Path(sys.argv[1])
    research_dir = skill_dir / 'references' / 'research'

    if not research_dir.exists():
        print(f"❌ 目录不存在: {research_dir}")
        sys.exit(1)

    files = {}
    rows = []
    total_sources = 0
    missing = []

    for key, label in AGENTS.items():
        md_file = research_dir / f"{key}.md"
        if not md_file.exists():
            missing.append(label)
            rows.append(f"│ {label:<12} │ {'❌ 缺失':<8} │ {'—':<24} │")
            continue

        content = md_file.read_text(encoding='utf-8')
        files[key] = content
        stats = count_sources(content)
        findings = extract_key_findings(content)

        total_sources += stats['unique_urls']

        findings_str = ', '.join(findings) if findings else '—'
        if len(findings_str) > 40:
            findings_str = findings_str[:37] + '...'

        rows.append(f"│ {label:<12} │ {stats['unique_urls']:<8} │ {findings_str:<24} │")

    # 矛盾检测
    contradictions = find_contradictions(files)

    # 输出
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

    if missing:
        print(f"│ 信息不足维度   │ {len(missing)}个      │ {', '.join(missing):<24} │")
    else:
        print(f"│ 信息不足维度   │ 无       │ {'—':<24} │")

    print("└──────────────┴──────────┴──────────────────────────┘")

    # 总结
    if total_sources < 5:
        print("\n⚠️ 引用 BV 数量极少，建议检查 Stage 3 的数据产出量。")
    if missing:
        print(f"\n⚠️ 缺失维度: {', '.join(missing)}，建议排查 stage4 数据变压器的输出。")


if __name__ == '__main__':
    main()