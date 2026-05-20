#!/usr/bin/env python3
"""
Mirror 蒸馏阶段 5：大模型认知技能生成 (Skill Generation)
读取 Stage 4 生成的 6 维度认知调研报告、方法论文档与 skill-template.md，
采用“单次直渲染”方式生成最终的 SKILL.md。
"""

import os
import sys
import asyncio
import json
from datetime import datetime
from typing import Optional, Tuple

try:
    from openai import AsyncOpenAI
except ImportError:
    print("请安装 openai 库: pip install openai")
    sys.exit(1)

# ==================== 全局配置 ====================
DATA_DIR = "data"
ACCOUNT_DIR = os.path.join(DATA_DIR, "account")
DEEPSEEK_CONFIG_PATH = os.path.join(ACCOUNT_DIR, "deepseek_config.json")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

PERSONA_DIR = os.path.join(DATA_DIR, "stage4_persona_builder")
PERSONA_HISTORY_DIR = os.path.join(PERSONA_DIR, "history")
RESEARCH_DIR = os.path.join(PERSONA_DIR, "references", "research")
SKILL_OUTPUT_PATH = os.path.join(PERSONA_DIR, "SKILL.md")
TEMPLATE_PATH = os.path.join("references", "skill-template.md")
FRAMEWORK_PATH = os.path.join("references", "extraction-framework.md")
os.makedirs(PERSONA_HISTORY_DIR, exist_ok=True)

RESEARCH_FILES = [
    "01-core-consumption.md",
    "02-value-resonances.md",
    "03-expression-dna.md",
    "04-boundaries-rejections.md",
    "05-decision-heuristics.md",
    "06-timeline.md",
]

MODEL_NAME = "deepseek-reasoner"


def load_deepseek_config() -> Tuple[str, str]:
    if not os.path.exists(DEEPSEEK_CONFIG_PATH):
        print(f"[FATAL] 找不到 DeepSeek 配置文件: {DEEPSEEK_CONFIG_PATH}")
        sys.exit(1)
    with open(DEEPSEEK_CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
    api_key = config.get("api_key", "").strip()
    if not api_key:
        print("[FATAL] DeepSeek 配置文件中缺少 api_key")
        sys.exit(1)
    base_url = config.get("base_url", "").strip() or DEEPSEEK_BASE_URL
    return api_key, base_url


def read_file(filepath: str) -> str:
    if not os.path.exists(filepath):
        print(f"[WARN] 文件不存在: {filepath}")
        return ""
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


def strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```json"):
        text = text[len("```json"):].strip()
    elif text.startswith("```markdown"):
        text = text[len("```markdown"):].strip()
    elif text.startswith("```"):
        text = text[len("```"):].strip()
    if text.endswith("```"):
        text = text[:-3].strip()
    return text


def clean_markdown_output(text: str) -> str:
    return strip_code_fences(text)


def write_skill_variants(skill_md: str, run_timestamp: str) -> tuple[str, str]:
    latest_path = SKILL_OUTPUT_PATH
    archive_dir = os.path.join(PERSONA_HISTORY_DIR, run_timestamp)
    os.makedirs(archive_dir, exist_ok=True)
    archive_path = os.path.join(archive_dir, "SKILL.md")

    for target_path in [latest_path, archive_path]:
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(skill_md)

    return latest_path, archive_path


async def call_llm(
    client: AsyncOpenAI,
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int,
    temperature: float,
) -> Optional[str]:
    request_kwargs = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    try:
        response = await client.chat.completions.create(**request_kwargs)
        return response.choices[0].message.content or ""
    except Exception as e:
        print(f"[ERROR] LLM 调用失败: {e}")
        return None


async def generate_skill_markdown(
    client: AsyncOpenAI,
    framework_text: str,
    research_data: str,
    template: str,
) -> Optional[str]:
    system_prompt = (
        "你是 Mirror Distillation 的认知建模与渲染引擎。"
        "你的任务不是先做摘要再写人设，而是基于完整研究语料，直接生成最终的 SKILL.md。"
        "你必须严格遵守以下原则："
        "只依据提供的研究语料与方法论文档；"
        "禁止使用空泛哲学套话；"
        "禁止把单个作者的观点直接等同于用户自身的稳定心智模型；"
        "禁止因模板中的示例而复述示例式表达；"
        "禁止为追求文风或完整性而补写证据之外的结论；"
        "如果证据不足，必须在最终 Markdown 中保留谨慎表达或明确降级。"
    )

    user_prompt = f"""
以下是 Mirror 的方法论文档，请将其视为硬约束：
---
{framework_text}
---

以下是 Stage 4 输出的研究语料：
---
{research_data}
---

以下是 SKILL.md 模板，请将其视为最终输出结构：
---
{template}
---

硬性要求：
1. 直接输出最终 Markdown，不要先输出 JSON、提纲、思维链或中间草稿。
2. “核心心智模型”只能保留满足以下条件的候选：
   - 至少有跨领域复现
   - 能映射到新问题的决策应用
   - 主要由高价值证据支撑
3. 如果严格筛选后只剩 2 个模型，但仍能从其他高价值证据中提炼出第 3 个较弱但合格的模型，可保守写入；若确实不足 3 个，也不要编造。
4. 每个模型都必须写出：
   - 逻辑内核
   - 证据锚点
   - 跨域复现
   - 映射来源
   - 决策应用
   - 认知局限
5. “决策启发式”必须是默认动作规则，不是空泛价值口号。
6. “表达 DNA”只能提炼多个来源的风格交集，不得复刻单个作者口头禅。
7. “候选排斥信息类型”必须明确体现其为镜像反推，而非显式事实。
8. 若时间证据不足，必须把“认知演化”降级为“近期关注切片”，不得伪造长期演化叙事。
9. 时间信息如已在研究语料中转为自然语言时间片，应优先使用自然语言时间片，不要复述原始 Unix 时间戳。
10. 模板中的方括号、示例与说明文字仅表示结构要求，不构成推荐答案。
11. 输出必须是纯 Markdown，不要添加任何解释。

生成策略：
- 先在脑中完成证据筛选，再直接按模板渲染。
- 若某结论无法满足方法论文档中的门槛，应放弃该结论而不是补写。
- 最终结果应更像“可调用的认知操作系统”，而不是“聪明人风格简介”。
"""

    print("[LLM] 正在基于完整研究语料直渲染 SKILL.md...")
    raw_content = await call_llm(
        client,
        system_prompt,
        user_prompt,
        max_tokens=32000,
        temperature=0.2,
    )
    if not raw_content:
        return None
    return clean_markdown_output(raw_content)


async def generate_skill(
    api_key: str,
    base_url: str,
    framework_text: str,
    research_data: str,
    template: str,
) -> Optional[str]:
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    return await generate_skill_markdown(client, framework_text, research_data, template)


async def main():
    print("=" * 60)
    print(" 🧠 [Mirror Distillation] 启动阶段 5：认知技能生成 (Skill Gen)")
    print("=" * 60)

    api_key, base_url = load_deepseek_config()

    print("[INFO] 正在读取调研报告...")
    all_research_content = ""
    for filename in RESEARCH_FILES:
        filepath = os.path.join(RESEARCH_DIR, filename)
        content = read_file(filepath)
        if content:
            all_research_content += f"\n\n--- FILE: {filename} ---\n\n{content}"

    if not all_research_content.strip():
        print("[ERROR] 未找到任何有效的调研报告数据，请先运行 Stage 4。")
        return

    print(f"[INFO] 正在读取模板: {TEMPLATE_PATH}")
    template_content = read_file(TEMPLATE_PATH)
    if not template_content:
        print("[ERROR] 找不到 SKILL.md 模板文件。")
        return

    print(f"[INFO] 正在读取方法论文档: {FRAMEWORK_PATH}")
    framework_content = read_file(FRAMEWORK_PATH)
    if not framework_content:
        print("[ERROR] 找不到方法论文档 extraction-framework.md。")
        return

    skill_md = await generate_skill(
        api_key,
        base_url,
        framework_content,
        all_research_content,
        template_content,
    )

    if skill_md is None:
        print("\n[FAIL] 技能生成失败。")
        return

    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    latest_path, archive_path = write_skill_variants(skill_md, run_timestamp)

    print("\n[SUCCESS] 认知技能文件已生成！")
    print(f"  📂 Latest: {latest_path}")
    print(f"  🗃️ Archive: {archive_path}")
    print("\n[NEXT STEP] 接下来建议运行 stage5_quality_check.py 进行验收。")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
