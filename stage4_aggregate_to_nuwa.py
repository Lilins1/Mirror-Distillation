#!/usr/bin/env python3
"""
Mirror 蒸馏阶段 4 前置中间件：数据变压器
将 Stage 3 生成的高维 JSON 视频总结颗粒，重组为 Nuwa-Skill 所需的 6 维 Markdown 调研报告。
"""

import os
import sys
import json
import math
from datetime import datetime
from collections import defaultdict

# ==========================================
# 全局路径配置
# ==========================================
DATA_DIR = "data"
STAGE3_DIR = os.path.join(DATA_DIR, "stage3_summaries")
# Nuwa 框架的标准调研目录
NUWA_RESEARCH_DIR = os.path.join(DATA_DIR, "stage4_persona_builder", "references", "research")

os.makedirs(NUWA_RESEARCH_DIR, exist_ok=True)

# 仅选取排名前 0.3% 的核心知识点进入最终推演，避免杂音污染
TOP_PERCENTILE = 0.3  

def recalculate_advanced_cif(node):
    """
    联合分布 CIF 算法：隐性投入(对数时长 x 完播率) * 内容价值乘数 + 显性互动基数
    """
    metadata = node.get('metadata', {})
    ai_data = node.get('ai_distillation', {})
    
    # 基础指标
    duration = max(metadata.get('duration', 1), 1)
    progress = metadata.get('progress', 0)
    completion_rate = min(progress / duration, 1.2) # 允许最大 20% 的倒退重复观看溢出
    knowledge_score = ai_data.get('knowledge_value_score', 1)
    
    # Stage 1 传过来的原始 CIF 已经包含了点赞/投币/收藏的权重加成
    # 这里我们将其作为 Explicit Bonus 基数
    explicit_bonus = float(node.get('cognitive_impact_factor', 1.0))
    
    # 核心对数公式：抑制极长视频的线性膨胀，放大深度学习的权重
    base_behavior = 2.0 * math.log(duration + 1) * completion_rate
    content_multiplier = 1.0 + (0.1 * knowledge_score)
    
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
                # 仅保留成功提炼且非广告污染的视频
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
    """过滤掉大模型输出的 'insufficient_data' 占位符"""
    if not text or not isinstance(text, str): return False
    text_lower = text.lower()
    return "insufficient_data" not in text_lower and "无法推断" not in text_lower

# ==========================================
# Nuwa 6 维 Markdown 生成器
# ==========================================

def generate_01_core_consumption(nodes):
    md = "# 01. 核心知识摄入域 (Core Consumption)\n\n"
    md += "> 数据源：基于联合公式计算得出的 Top 30% 高优视频总结。\n\n"
    
    categories = defaultdict(list)
    for node in nodes:
        cat = node['ai_distillation'].get('tags', {}).get('primary_category', '未分类领域')
        title = node['metadata'].get('title', '未知')
        summary = node['ai_distillation'].get('summary', '').replace('\n', ' ')
        cif = node['computed_cif']
        categories[cat].append(f"- **[BV:{node['video_id']} | CIF: {cif:.1f}] {title}**\n  *摘要*: {summary}")
        
    for cat, items in categories.items():
        md += f"## 领域：{cat}\n"
        md += "\n\n".join(items[:15]) + "\n\n" # 每个领域截断防止 Token 爆炸
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
            if count >= 30: break # 取前30条高质量共鸣即可
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

def generate_04_boundaries_rejections():
    md = "# 04. 认知排斥区与诚实边界 (Boundaries & Rejections)\n\n"
    md += "### [系统级推理指令 - 极其重要]\n\n"
    md += "> 注意：由于本系统采用严格的无干预隐私抓取，无法获得用户确切的“拉黑”或“不感兴趣”操作记录。\n"
    md += "> \n"
    md += "> **作为顶尖的认知建模师，请你执行【镜像反演推断】：**\n"
    md += "> 请基于 `01` 到 `03` 文件中揭示的该用户的“极端热爱域”与“核心心智模型”（例如：极端重视逻辑自洽、追求交叉数据支撑等），利用逆向心理学，合理推断出该用户**绝对排斥**的信息源类型、论证方式或情绪氛围（例如：煽动情绪的伪科学、毫无根据的绝对断言）。\n"
    md += "> \n"
    md += "> 将这些推断结果设定为数字分身的“价值观底线”与“反模式 (Anti-patterns)”。\n"
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
    md = "# 06. 认知演化线 (Cognitive Evolution Timeline)\n\n"
    md += "> 数据源：按时间序列梳理用户近期的关注点迁移。\n\n"
    
    # 按时间戳从早到晚排序
    sorted_by_time = sorted([n for n in nodes if n['metadata'].get('view_at')], key=lambda x: x['metadata']['view_at'])
    
    timeline = defaultdict(list)
    for node in sorted_by_time:
        view_time = datetime.fromtimestamp(node['metadata']['view_at'])
        month_key = view_time.strftime("%Y年%m月")
        cat = node['ai_distillation'].get('tags', {}).get('primary_category', '未分类')
        title = node['metadata'].get('title', '')
        timeline[month_key].append(f"[{cat}] {title}")
        
    for month, items in timeline.items():
        md += f"### {month}\n"
        # 每个月随机取 5 个代表性摄入，反映当月求知轮廓
        for item in items[:5]:
            md += f"- 重点摄入: {item}\n"
        md += "\n"
    return md

def main():
    print("="*60)
    print(" 🪞 [Mirror Distillation] 启动数据变压器 (Adapter)")
    print("="*60)
    
    all_nodes = load_and_score_nodes()
    total_count = len(all_nodes)
    if total_count == 0:
        print("[ERROR] 找不到任何有效的 Stage 3 JSON 总结数据！")
        sys.exit(1)
        
    top_limit = max(10, int(total_count * TOP_PERCENTILE))
    high_value_nodes = all_nodes[:top_limit]
    
    print(f"[INFO] 成功加载 {total_count} 个认知节点，已截取前 {top_limit} 个 (Top {TOP_PERCENTILE*100}%) 高价值节点用于最终蒸馏。")
    print(f"[INFO] 正在生成 Nuwa 兼容的 6 维 Markdown 报告...\n")
    
    reports = {
        "01-core-consumption.md": generate_01_core_consumption(high_value_nodes),
        "02-value-resonances.md": generate_02_value_resonances(high_value_nodes),
        "03-expression-dna.md": generate_03_expression_dna(high_value_nodes),
        "04-boundaries-rejections.md": generate_04_boundaries_rejections(),
        "05-decision-heuristics.md": generate_05_decision_heuristics(high_value_nodes),
        "06-timeline.md": generate_06_timeline(high_value_nodes)
    }
    
    for filename, content in reports.items():
        filepath = os.path.join(NUWA_RESEARCH_DIR, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"  ✅ 已生成: {filename}")
        
    print("\n[SUCCESS] 变压完成！所有报告已输出至:")
    print(f"  📂 {NUWA_RESEARCH_DIR}/")
    print("\n[NEXT STEP] 接下来，您可以直接调用大模型，要求其阅读这 6 个文件，并按照女娲的 `skill-template.md` 规范输出最终的数字生命文件 `SKILL.md`！")

if __name__ == '__main__':
    main()