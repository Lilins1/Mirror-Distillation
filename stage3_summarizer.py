import os
import sys
import json
import asyncio
import time
import random
import math
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple

try:
    from openai import AsyncOpenAI
except ImportError:
    print("请安装 openai 库: pip install openai")
    sys.exit(1)

# ==================== 全局目录与配置 ====================
DATA_DIR = "data"
ACCOUNT_DIR = os.path.join(DATA_DIR, "account")
STAGE2_DIR = os.path.join(DATA_DIR, "stage2_subtitles")
SUBTITLES_DIR = os.path.join(STAGE2_DIR, "parsed_videos")
STAGE3_DIR = os.path.join(DATA_DIR, "stage3_summaries")
os.makedirs(STAGE3_DIR, exist_ok=True)

PROGRESS_FILE = os.path.join(STAGE3_DIR, "stage3_progress.json")
DEEPSEEK_CONFIG_PATH = os.path.join(ACCOUNT_DIR, "deepseek_config.json")

# 可被管线总控台覆盖的参数
DEBUG_MODE = False
DEBUG_ITEM_LIMIT = 5
CONCURRENCY_LIMIT = 1

# DeepSeek API 基础地址（固定）
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

# 模型选择相关阈值（可根据实际效果调整）
HIGH_VALUE_THRESHOLD = 6.0        # 当 impact_score * log(len+1) > 此值时，启用大模型
MODEL_SMALL = "deepseek-chat"      # 标准模型（64k上下文，性价比高）
MODEL_LARGE = "deepseek-reasoner"  # 推理增强模型（64k上下文，适合高价值长文）

# 提示词截断控制（保护上下文窗口）
MAX_INPUT_CHARS = 40000            # 输入给模型的字幕最大字符数（中文约对应30k token，安全边际）

# ==================== 工具函数 ====================
def safe_save_json(data: dict, filepath: str):
    """原子化保存 JSON 文件"""
    tmp_path = filepath + ".tmp"
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, filepath)

def load_progress() -> Dict[str, str]:
    """读取断点进度文件"""
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def load_deepseek_config() -> Tuple[str, str]:
    """从 account 目录加载 DeepSeek 配置，返回 (api_key, base_url)"""
    if not os.path.exists(DEEPSEEK_CONFIG_PATH):
        print(f"[FATAL] 找不到 DeepSeek 配置文件: {DEEPSEEK_CONFIG_PATH}")
        print("请创建该文件，内容示例：")
        print('{"api_key": "sk-xxxx", "base_url": "https://api.deepseek.com/v1"}')
        sys.exit(1)
    with open(DEEPSEEK_CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = json.load(f)
    api_key = config.get("api_key", "").strip()
    if not api_key:
        print("[FATAL] DeepSeek 配置文件中缺少 api_key")
        sys.exit(1)
    # base_url 优先使用配置文件中的，否则用全局常量
    base_url = config.get("base_url", "").strip() or DEEPSEEK_BASE_URL
    return api_key, base_url

# ==================== 模型选择与提示词工厂 ====================
def select_model(impact_score: float, text_length: int) -> str:
    """
    根据视频价值分数与文本长度的乘积（对数平滑）动态选择模型
    impact_score: 来自 CIF 认知影响因子，假设为0-10的数值
    text_length: 字幕总字符数
    """
    # 计算对数平滑值，避免极长文本的乘积爆炸
    try:
        log_len = math.log(text_length + 1)
    except ValueError:
        log_len = 0.0
    score_metric = impact_score * log_len
    if score_metric > HIGH_VALUE_THRESHOLD:
        return MODEL_LARGE
    return MODEL_SMALL

def build_prompt(text: str, title: str, description: str, tags: list,
                 text_length: int, chosen_model: str) -> Tuple[str, str, int]:
    """
    根据文本长度动态选择总结策略，并适配模型上下文窗口
    返回：(system_prompt, user_content, max_tokens)
    """
    # 1. 选择总结模式与长度指引
    if text_length <= 1500:
        mode = "short_summary"
        length_guideline = "100-200字，只保留最核心的观点与结论。"
        max_tokens = 600
    elif text_length <= 8000:
        mode = "medium_summary"
        length_guideline = "300-500字，按逻辑分段，包含主要论点和关键细节。"
        max_tokens = 1500
    else:
        mode = "deep_structured"
        length_guideline = "不少于800字，采用结构化格式（如：背景-问题-分析-结论），充分保留知识颗粒度。"
        max_tokens = 4000

    # 2. 动态截取字幕（保留头部，避免丢失关键信息，尾部信息量通常较低但保留完整性）
    truncated_text = text[:MAX_INPUT_CHARS]
    if len(text) > MAX_INPUT_CHARS:
        trunc_note = f"\n[注意：原字幕总长{len(text)}字，已截取前{MAX_INPUT_CHARS}字进行总结。]"
        truncated_text += trunc_note

    # 3. 系统角色设定
    system_prompt = (
        "你是一个顶级的知识蒸馏专家，擅长从视频文稿中提取高密度认知内容。\n"
        "请严格遵守以下规则：\n"
        "1. 仅基于提供的字幕/文本内容，不要引入外部知识。\n"
        "2. 自然过滤掉明显的广告口播、赞助内容、求三连等非知识信息。\n"
        "3. 使用中文输出，保持专业且平实的语气。\n"
    )

    # 4. 用户任务描述
    user_content = f"""
视频标题：{title}
视频简介：{description}
原始标签：{', '.join(tags) if tags else '无'}
字幕全文（已自动去除时间轴）：
---
{truncated_text}
---

请完成以下任务并**严格按JSON格式输出**（不要添加任何其他文字）：
{{
  "mode": "{mode}",
  "summary": "你生成的结构化总结文本，长度要求：{length_guideline}",
  "tags": {{
    "primary_category": "一级分类，如：科技/人文/教育/娱乐/生活/商业/其他",
    "secondary_category": "二级分类（细化）",
    "keywords": ["关键词1", "关键词2", "关键词3", ...]
  }},
  "knowledge_value_score": 1-10的整数，表示本文的知识密度与价值，
  "is_ad_contaminated": true/false，若字幕中混杂大量无关推广内容则标记为true
}}
"""
    return system_prompt, user_content, max_tokens

# ==================== DeepSeek 调用封装 ====================
async def call_deepseek(api_key: str, base_url: str, model: str,
                        system: str, user: str, max_tokens: int,
                        temperature: float = 0.3, retry: int = 3) -> Optional[dict]:
    """异步调用 DeepSeek，自动重试与限流处理"""
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    for attempt in range(retry):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user}
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"}  # 启用 JSON 模式
            )
            content = response.choices[0].message.content
            return json.loads(content)
        except json.JSONDecodeError as e:
            print(f"  [WARN] JSON解析失败，尝试 {attempt+1}/{retry}: {e}")
            await asyncio.sleep(2 * (attempt + 1))
        except Exception as e:
            err_msg = str(e).lower()
            print(f"  [ERROR] API异常: {e}")
            if "rate" in err_msg or "429" in err_msg:
                # 速率限制，长休眠
                sleep_time = 10 * (attempt + 1)
                print(f"  [RATE] 触发限流，休眠 {sleep_time} 秒...")
                await asyncio.sleep(sleep_time)
            elif "context" in err_msg or "length" in err_msg:
                # 上下文过长，不可恢复，直接返回空
                print(f"  [FATAL] 输入超长，跳过此视频。")
                return None
            else:
                await asyncio.sleep(3 * (attempt + 1))
    return None

# ==================== 单个视频处理任务 ====================
async def process_summary(bvid: str, node: dict, progress_cache: dict,
                          semaphore: asyncio.Semaphore, start_time: float,
                          completed: list, lock: asyncio.Lock, total: int,
                          api_key: str, base_url: str):
    async with semaphore:
        # 断点检查
        if bvid in progress_cache:
            return

        subtitle_path = os.path.join(SUBTITLES_DIR, f"{bvid}.json")
        if not os.path.exists(subtitle_path):
            print(f"  [SKIP] {bvid} 无字幕文件")
            progress_cache[bvid] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            safe_save_json(progress_cache, PROGRESS_FILE)
            async with lock:
                completed[0] += 1
            return

        with open(subtitle_path, 'r', encoding='utf-8') as f:
            sub_data = json.load(f)

        full_text = sub_data.get("full_text", "").strip()
        if not full_text:
            print(f"  [SKIP] {bvid} 字幕为空")
            progress_cache[bvid] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            safe_save_json(progress_cache, PROGRESS_FILE)
            async with lock:
                completed[0] += 1
            return

        # 提取元数据与 CIF 影响因子
        metadata = node.get("metadata", {})
        title = metadata.get("title", "")
        desc = metadata.get("description", "")
        tags = metadata.get("tags", [])
        cif = node.get("cognitive_impact_factor", {})
        # 尝试获取综合影响分数（根据实际CIF数据结构调整字段名）
        impact_score = cif.get("impact_score", 5.0)   # 默认中等分数
        try:
            impact_score = float(impact_score)
        except (ValueError, TypeError):
            impact_score = 5.0

        text_len = len(full_text)

        # 动态选择模型
        chosen_model = select_model(impact_score, text_len)
        print(f"\n[Stage3] 处理: {bvid} | {title[:20]}... "
              f"(字数:{text_len}, CIF:{impact_score:.1f}, 模型:{chosen_model})")

        # 构建提示词并获取最大输出token数
        system_prompt, user_prompt, max_tok = build_prompt(
            full_text, title, desc, tags, text_len, chosen_model
        )

        # 调用大模型
        ai_result = await call_deepseek(api_key, base_url, chosen_model,
                                        system_prompt, user_prompt, max_tok)

        if not ai_result:
            print(f"  [FAIL] {bvid} 总结失败，记录空结果。")
            ai_result = {
                "mode": "failed",
                "summary": "",
                "tags": {},
                "knowledge_value_score": 0,
                "is_ad_contaminated": False
            }

        # 组装最终知识颗粒
        knowledge_grain = {
            "video_id": bvid,
            "metadata": metadata,
            "cognitive_impact_factor": cif,
            "sponsor_block_info": sub_data.get("processing_status", {}).get("sponsor_block", {}),
            "ai_distillation": {
                "model": chosen_model,
                "timestamp": datetime.now().isoformat(),
                "mode": ai_result.get("mode", "unknown"),
                "summary": ai_result.get("summary", ""),
                "tags": ai_result.get("tags", {}),
                "knowledge_value_score": ai_result.get("knowledge_value_score", 0),
                "is_ad_contaminated": ai_result.get("is_ad_contaminated", False)
            }
        }

        out_path = os.path.join(STAGE3_DIR, f"{bvid}.json")
        safe_save_json(knowledge_grain, out_path)

        # 保存断点
        progress_cache[bvid] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        safe_save_json(progress_cache, PROGRESS_FILE)

        # 进度报告与预估
        async with lock:
            completed[0] += 1
            elapsed = time.time() - start_time
            avg = elapsed / completed[0] if completed[0] > 0 else 0
            remaining = (total - completed[0]) * avg
            pct = (completed[0] / total) * 100
            print(f"  [PROG] {completed[0]}/{total} ({pct:.1f}%) | 预计剩余: {remaining/60:.1f}分钟")

        # API 调用间隔，防止触发限流
        await asyncio.sleep(random.uniform(5.0, 12.0))

# ==================== 主入口 ====================
async def main():
    # 加载 DeepSeek 密钥
    api_key, base_url = load_deepseek_config()
    print(f"[CONFIG] DeepSeek base_url: {base_url}")

    # 读取 stage1 的 CIF 主表
    INPUT_FILE = os.path.join(DATA_DIR, "stage1_enrich", "master_enriched.json")
    if not os.path.exists(INPUT_FILE):
        print(f"[ERROR] 找不到提纯表: {INPUT_FILE}")
        return

    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        master_enriched = json.load(f)

    progress_cache = load_progress()

    # 筛选待处理视频（已提取有效字幕且未完成总结）
    pending_items = []
    for bvid, node in master_enriched.items():
        if bvid in progress_cache:
            continue
        sub_path = os.path.join(SUBTITLES_DIR, f"{bvid}.json")
        if not os.path.exists(sub_path):
            continue
        try:
            with open(sub_path, 'r', encoding='utf-8') as f:
                sub_json = json.load(f)
            if sub_json.get("full_text", "").strip():
                pending_items.append((bvid, node))
        except:
            pass

    if DEBUG_MODE:
        pending_items = pending_items[:DEBUG_ITEM_LIMIT]

    if not pending_items:
        print("[INFO] 所有视频总结已完成，无增量任务。")
        return

    print(f"\n[STAGE 3] 将处理 {len(pending_items)} 个视频（模型策略：按 CIF×log 动态选择）")
    print(f"[MODEL] 标准模型: {MODEL_SMALL} | 增强模型: {MODEL_LARGE} | 阈值: {HIGH_VALUE_THRESHOLD}")

    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
    start_time = time.time()
    completed = [0]
    lock = asyncio.Lock()

    tasks = [
        process_summary(bvid, node, progress_cache, semaphore,
                        start_time, completed, lock, len(pending_items),
                        api_key, base_url)
        for bvid, node in pending_items
    ]
    await asyncio.gather(*tasks)

    total_min = (time.time() - start_time) / 60
    print(f"\n[SUCCESS] 第三阶段完成！总用时: {total_min:.1f}分钟")
    print(f"最终知识颗粒输出目录: {STAGE3_DIR}/")

if __name__ == '__main__':
    if sys.platform == 'win32':
        os.system('chcp 65001')
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())