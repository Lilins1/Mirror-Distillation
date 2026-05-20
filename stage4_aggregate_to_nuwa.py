#!/usr/bin/env python3
"""
Mirror 蒸馏阶段 4 前置中间件：数据变压器
将 Stage 3 生成的高维 JSON 视频总结颗粒，重组为 Nuwa-Skill 所需的 6 维 Markdown 调研报告。
支持独立运行或通过 pipeline_runner 注入超参数执行。
"""

import os
import sys
import json
import math
import asyncio
from datetime import datetime
from collections import defaultdict

# ==========================================
# 全局路径与可注入配置
# ==========================================
DATA_DIR = "data"
STAGE3_DIR = os.path.join(DATA_DIR, "stage3_summaries")
PERSONA_DIR = os.path.join(DATA_DIR, "stage4_persona_builder")
NUWA_RESEARCH_DIR = os.path.join(DATA_DIR, "stage4_persona_builder", "references", "research")
PERSONA_HISTORY_DIR = os.path.join(PERSONA_DIR, "history")
os.makedirs(NUWA_RESEARCH_DIR, exist_ok=True)
os.makedirs(PERSONA_HISTORY_DIR, exist_ok=True)

# 以下超参数可由 pipeline_runner 覆盖注入
TOP_PERCENTILE = 0.3                      # 仅选取排名前百分比的核心知识点
CIF_COMPLETION_RATE_MAX_CAP = 1.2         # 允许最大倒退重复观看溢出比例
CIF_KNOWLEDGE_SCORE_WEIGHT = 0.1          # 知识价值分数对整体公式的乘数权重
CIF_BASE_BEHAVIOR_WEIGHT = 2.0            # 行为基数权重

def recalculate_advanced_cif(node):
    """
    联合分布 CIF 算法：隐性投入(对数时长 x 完播率) * 内容价值乘数 + 显性互动基数
    """
    metadata = node.get('metadata', {})
    ai_data = node.get('ai_distillation', {})
    
    # 基础指标
    duration = max(metadata.get('duration', 1), 1)
    progress = metadata.get('progress', 0)
    completion_rate = min(progress / duration, CIF_COMPLETION_RATE_MAX_CAP) 
    knowledge_score = ai_data.get('knowledge_value_score', 1)
    
    # Stage 1 传过来的原始 CIF (包含点赞/投币/收藏的权重加成)
    explicit_bonus = float(node.get('cognitive_impact_factor', 1.0))
    
    # 核心公式使用注入的超参数
    base_behavior = CIF_BASE_BEHAVIOR_WEIGHT * math.log(duration + 1) * completion_rate
    content_multiplier = 0.5 + (CIF_KNOWLEDGE_SCORE_WEIGHT * knowledge_score)
    
    final_cif = (base_behavior * content_multiplier) + explicit_bonus
    return round(final_cif, 3)

def load_and_score_nodes():
    """加载所有 Stage 3 数据并按新的 CIF 算法打分排序"""
    if not os.path.exists(STAGE3_DIR):
        print(f"[FATAL] 找不到阶段三输出目录: {STAGE3_DIR}")
        sys.exit(1)
        
    nodes = []
    for filename in os.listdir(STAGE3_DIR):
        if not filename.endswith('.json'):
            continue
        filepath = os.path.join(STAGE3_DIR, filename)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if data.get('ai_distillation', {}).get('mode') != 'failed' and \
                   not data.get('ai_distillation', {}).get('is_ad_contaminated', False):
                    data['computed_cif'] = recalculate_advanced_cif(data)
                    nodes.append(data)
        except Exception as e:
            print(f"[WARN] 文件解析失败 {filename}: {e}")
            
    # 按综合 CIF 降序排列
    nodes.sort(key=lambda x: x['computed_cif'], reverse=True)
    return nodes

def extract_valid_text(text):
    if not text or not isinstance(text, str): return False
    text_lower = text.lower()
    return "insufficient_data" not in text_lower and "无法推断" not in text_lower


def compact_text(text, max_len=140):
    if not text or not isinstance(text, str):
        return ""
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def write_report_variants(filename, content, run_timestamp):
    latest_path = os.path.join(NUWA_RESEARCH_DIR, filename)
    archive_dir = os.path.join(PERSONA_HISTORY_DIR, run_timestamp, "references", "research")
    os.makedirs(archive_dir, exist_ok=True)
    archive_path = os.path.join(archive_dir, filename)

    for target_path in [latest_path, archive_path]:
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(content)

    return latest_path, archive_path

# ==========================================
# Nuwa 6 维 Markdown 生成器
# ==========================================

def generate_01_core_consumption(nodes):
    md = "# 01. 核心知识摄入域 (Core Consumption)\n\n"
    md += f"> 数据源：基于联合公式计算得出的 Top {TOP_PERCENTILE*100}% 高优视频总结。\n\n"
    categories = defaultdict(list)
    for node in nodes:
        cat = node['ai_distillation'].get('tags', {}).get('primary_category', '未分类领域')
        title = node['metadata'].get('title', '未知')
        summary = node['ai_distillation'].get('summary', '').replace('\n', ' ')
        cif = node['computed_cif']
        categories[cat].append(f"- **[BV:{node['video_id']} | CIF: {cif:.1f}] {title}**\n  *摘要*: {summary}")
    for cat, items in categories.items():
        md += f"## 领域：{cat}\n"
        md += "\n\n".join(items[:15]) + "\n\n" 
    return md

def generate_02_value_resonances(nodes):
    md = "# 02. 价值共鸣点 (Value Resonances)\n\n"
    md += "> 数据源：提取用户高度认同视频中的底层信念与价值观偏好。\n\n"
    count = 0
    for node in nodes:
        profile = node.get('ai_distillation', {}).get('cognitive_profile', {})
        beliefs = profile.get('core_beliefs', '')
        values = profile.get('values_preferences', '')
        if extract_valid_text(beliefs) or extract_valid_text(values):
            md += f"### 来源 BV:{node['video_id']} (CIF: {node['computed_cif']:.1f})\n"
            if extract_valid_text(beliefs): md += f"- **底层信念**: {beliefs}\n"
            if extract_valid_text(values): md += f"- **价值偏好**: {values}\n"
            md += "\n"
            count += 1
            if count >= 30: break 
    return md

def generate_03_expression_dna(nodes):
    md = "# 03. 表达DNA映射 (Expression DNA)\n\n"
    md += "> 数据源：通过分析用户最沉浸、最认可的知识区 UP 主的语言风格与情绪基调，反向推演其内心理想的叙事节奏。\n\n"
    count = 0
    for node in nodes:
        profile = node.get('ai_distillation', {}).get('cognitive_profile', {})
        style = profile.get('language_style', '')
        tone = profile.get('emotional_tone', '')
        if extract_valid_text(style) or extract_valid_text(tone):
            md += f"- **BV:{node['video_id']} 特征**: {style} | {tone}\n"
            count += 1
            if count >= 40: break
    return md

def generate_04_boundaries_rejections(nodes):
    md = "# 04. 认知排斥区与诚实边界 (Boundaries & Rejections)\n\n"
    md += "> 数据定位：本文件不直接给出结论式“排斥区”，而是整理可用于反推认知边界的证据与约束。\n\n"

    md += "## 数据限制\n"
    md += "- 本系统没有显式的“拉黑 / 不感兴趣 / 点踩”记录。\n"
    md += "- 因此，任何“排斥区”都只能由正向高频偏好做镜像反推，不能视为显式事实。\n"
    md += "- 若证据仅来自单一视频、单一领域或单一作者，应视为弱信号，不宜升格为稳定边界。\n\n"

    md += "## 边界推断约束\n"
    md += "- 仅允许从重复出现的价值偏好、表达偏好与决策偏好中反推认知边界。\n"
    md += "- 仅当同类倾向跨领域复现时，才可视为候选反模式。\n"
    md += "- 对所有反推结果，均应保留“推断性质”标记，避免写成确定事实。\n"
    md += "- 若证据不足，应明确写“暂无稳定排斥区”，而不是补充想象性结论。\n\n"

    evidence_sections = {
        "价值与信念证据": [],
        "表达偏好证据": [],
        "决策偏好证据": [],
    }

    for node in nodes:
        profile = node.get("ai_distillation", {}).get("cognitive_profile", {})
        bvid = node.get("video_id", "")
        category = node.get("ai_distillation", {}).get("tags", {}).get("primary_category", "未分类")
        cif = node.get("computed_cif", 0)

        beliefs = profile.get("core_beliefs", "")
        values = profile.get("values_preferences", "")
        if extract_valid_text(beliefs) or extract_valid_text(values):
            text_parts = []
            if extract_valid_text(beliefs):
                text_parts.append(f"底层信念: {compact_text(beliefs)}")
            if extract_valid_text(values):
                text_parts.append(f"价值偏好: {compact_text(values)}")
            evidence_sections["价值与信念证据"].append(
                f"- BV:{bvid} | 分类:{category} | CIF:{cif:.1f} | " + " | ".join(text_parts)
            )

        style = profile.get("language_style", "")
        tone = profile.get("emotional_tone", "")
        if extract_valid_text(style) or extract_valid_text(tone):
            text_parts = []
            if extract_valid_text(style):
                text_parts.append(f"语言风格: {compact_text(style)}")
            if extract_valid_text(tone):
                text_parts.append(f"情绪基调: {compact_text(tone)}")
            evidence_sections["表达偏好证据"].append(
                f"- BV:{bvid} | 分类:{category} | CIF:{cif:.1f} | " + " | ".join(text_parts)
            )

        thinking = profile.get("thinking_mode", "")
        decision = profile.get("decision_pattern", "")
        if extract_valid_text(thinking) or extract_valid_text(decision):
            text_parts = []
            if extract_valid_text(thinking):
                text_parts.append(f"思维方式: {compact_text(thinking)}")
            if extract_valid_text(decision):
                text_parts.append(f"决策模式: {compact_text(decision)}")
            evidence_sections["决策偏好证据"].append(
                f"- BV:{bvid} | 分类:{category} | CIF:{cif:.1f} | " + " | ".join(text_parts)
            )

    for section_name, items in evidence_sections.items():
        md += f"## {section_name}\n"
        if items:
            md += "\n".join(items[:8]) + "\n\n"
        else:
            md += "- 暂无足够证据。\n\n"

    md += "## 使用建议\n"
    md += "- 本文件适合作为 Stage 5 的“边界证据输入”，而不是直接复制为最终结论。\n"
    md += "- 最终生成 SKILL.md 时，应优先提取跨领域重复出现的稳定信号，并保留不确定性措辞。\n"
    return md

def generate_05_decision_heuristics(nodes):
    md = "# 05. 推演决策树 (Decision Heuristics)\n\n"
    md += "> 数据源：提取深度视频中展现的思考模式与决策权重原则。\n\n"
    count = 0
    for node in nodes:
        profile = node.get('ai_distillation', {}).get('cognitive_profile', {})
        thinking = profile.get('thinking_mode', '')
        decision = profile.get('decision_pattern', '')
        framework = profile.get('knowledge_framework', '')
        if extract_valid_text(thinking) or extract_valid_text(decision) or extract_valid_text(framework):
            md += f"### 来源 BV:{node['video_id']} (分类: {node['ai_distillation'].get('tags', {}).get('primary_category', '')})\n"
            if extract_valid_text(thinking): md += f"- **思维方式**: {thinking}\n"
            if extract_valid_text(framework): md += f"- **运用框架**: {framework}\n"
            if extract_valid_text(decision): md += f"- **决策模式**: {decision}\n"
            md += "\n"
            count += 1
            if count >= 30: break
    return md

def generate_06_timeline(nodes):
    valid_nodes = [n for n in nodes if n.get("metadata", {}).get("view_at")]
    valid_nodes.sort(key=lambda x: x["metadata"]["view_at"])

    timeline = defaultdict(list)
    for node in valid_nodes:
        view_time = datetime.fromtimestamp(node["metadata"]["view_at"])
        month_key = view_time.strftime("%Y年%m月")
        timeline[month_key].append(node)

    month_keys = sorted(timeline.keys())
    enough_for_evolution = len(month_keys) >= 3

    title = "06. 认知演化线 (Cognitive Evolution Timeline)" if enough_for_evolution else "06. 近期认知切片 (Recent Cognitive Snapshot)"
    md = f"# {title}\n\n"
    md += "> 数据源：按时间序列梳理关注主题；仅当时间跨度足够时，才谨慎讨论“演化”。\n\n"

    md += "## 数据充分性判断\n"
    md += f"- 有效时间片数量: {len(month_keys)}\n"
    md += f"- 有效节点数量: {len(valid_nodes)}\n"
    md += f"- 是否足以支持“认知演化”判断: {'是' if enough_for_evolution else '否'}\n"
    if enough_for_evolution:
        md += "- 说明: 时间切片达到 3 个及以上，可进行低强度、谨慎的主题迁移观察。\n\n"
    else:
        md += "- 说明: 当前更适合描述“近期关注切片”，不宜写成稳定的长期演化结论。\n\n"

    for month in month_keys:
        month_nodes = timeline[month]
        category_scores = defaultdict(float)
        category_counts = defaultdict(int)

        for node in month_nodes:
            cat = node.get("ai_distillation", {}).get("tags", {}).get("primary_category", "未分类")
            category_scores[cat] += float(node.get("computed_cif", 0))
            category_counts[cat] += 1

        ranked_categories = sorted(
            category_scores.items(),
            key=lambda item: (item[1], category_counts[item[0]]),
            reverse=True,
        )
        ranked_nodes = sorted(month_nodes, key=lambda n: n.get("computed_cif", 0), reverse=True)

        md += f"## {month}\n"
        if ranked_categories:
            cat_parts = [
                f"{cat}(加权CIF={score:.1f}, 样本={category_counts[cat]})"
                for cat, score in ranked_categories[:3]
            ]
            md += f"- 高权重领域: {'；'.join(cat_parts)}\n"
        else:
            md += "- 高权重领域: 暂无足够数据\n"

        for node in ranked_nodes[:5]:
            cat = node.get("ai_distillation", {}).get("tags", {}).get("primary_category", "未分类")
            title_text = node.get("metadata", {}).get("title", "")
            md += f"- 代表样本: [{cat}] {title_text}\n"
        md += "\n"

    if enough_for_evolution:
        first_month = month_keys[0]
        last_month = month_keys[-1]
        first_top = {
            cat for cat, _ in sorted(
                ((cat, sum(n.get("computed_cif", 0) for n in timeline[first_month]
                  if n.get("ai_distillation", {}).get("tags", {}).get("primary_category", "未分类") == cat))
                 for cat in {n.get("ai_distillation", {}).get("tags", {}).get("primary_category", "未分类") for n in timeline[first_month]}),
                key=lambda item: item[1],
                reverse=True
            )[:3]
        }
        last_top = {
            cat for cat, _ in sorted(
                ((cat, sum(n.get("computed_cif", 0) for n in timeline[last_month]
                  if n.get("ai_distillation", {}).get("tags", {}).get("primary_category", "未分类") == cat))
                 for cat in {n.get("ai_distillation", {}).get("tags", {}).get("primary_category", "未分类") for n in timeline[last_month]}),
                key=lambda item: item[1],
                reverse=True
            )[:3]
        }

        new_topics = sorted(last_top - first_top)
        stable_topics = sorted(first_top & last_top)
        faded_topics = sorted(first_top - last_top)

        md += "## 观察结论（谨慎）\n"
        md += f"- 起始时间片: {first_month}；末端时间片: {last_month}\n"
        md += f"- 稳定出现的高权重主题: {', '.join(stable_topics) if stable_topics else '暂无明显稳定主题'}\n"
        md += f"- 末端新增的高权重主题: {', '.join(new_topics) if new_topics else '暂无明显新增主题'}\n"
        md += f"- 起始较强但末端减弱的主题: {', '.join(faded_topics) if faded_topics else '暂无明显减弱主题'}\n"
        md += "- 说明: 上述结论仅基于时间切片中的主题重心变化，不能直接等同于稳定的人格或价值观迁移。\n"
    return md

async def main():
    """定义为 async，兼容 pipeline_runner 的 await 调用"""
    print("="*60)
    print(" 🪞 [Mirror Distillation] 启动数据变压器 (Adapter)")
    print("="*60)
    
    all_nodes = load_and_score_nodes()
    total_count = len(all_nodes)
    if total_count == 0:
        print("[ERROR] 找不到任何有效的 Stage 3 JSON 总结数据！")
        return
        
    top_limit = max(10, int(total_count * TOP_PERCENTILE))
    high_value_nodes = all_nodes[:top_limit]
    
    print(f"[INFO] 成功加载 {total_count} 个认知节点，已截取前 {top_limit} 个 (Top {TOP_PERCENTILE*100}%) 高价值节点用于最终蒸馏。")
    print(f"[INFO] 当前计算参数: 知识权重={CIF_KNOWLEDGE_SCORE_WEIGHT}, 行为基数={CIF_BASE_BEHAVIOR_WEIGHT}")
    print(f"[INFO] 正在生成 Nuwa 兼容的 6 维 Markdown 报告...\n")
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    reports = {
        "01-core-consumption.md": generate_01_core_consumption(high_value_nodes),
        "02-value-resonances.md": generate_02_value_resonances(high_value_nodes),
        "03-expression-dna.md": generate_03_expression_dna(high_value_nodes),
        "04-boundaries-rejections.md": generate_04_boundaries_rejections(high_value_nodes),
        "05-decision-heuristics.md": generate_05_decision_heuristics(high_value_nodes),
        "06-timeline.md": generate_06_timeline(all_nodes)
    }
    
    for filename, content in reports.items():
        latest_path, archive_path = write_report_variants(filename, content, run_timestamp)
        print(f"  ✅ 已生成: {filename}")
        print(f"     Latest: {latest_path}")
        print(f"     Archive: {archive_path}")
        
    print("\n[SUCCESS] 变压完成！所有报告已输出至:")
    print(f"  📂 {NUWA_RESEARCH_DIR}/")
    print(f"  🗃️ 历史归档: {os.path.join(PERSONA_HISTORY_DIR, run_timestamp)}/")
    print("\n[NEXT STEP] 接下来，您可以直接调用大模型阅读这 6 个文件，并输出最终的 SKILL.md！")

if __name__ == '__main__':
    # 允许直接运行
    asyncio.run(main())
