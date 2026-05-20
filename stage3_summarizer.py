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

# ==================== 全局配置（可由总控台注入） ====================
DATA_DIR = "data"
ACCOUNT_DIR = os.path.join(DATA_DIR, "account")
STAGE2_DIR = os.path.join(DATA_DIR, "stage2_subtitles")
SUBTITLES_DIR = os.path.join(STAGE2_DIR, "parsed_videos")
STAGE3_DIR = os.path.join(DATA_DIR, "stage3_summaries")
os.makedirs(STAGE3_DIR, exist_ok=True)

PROGRESS_FILE = os.path.join(STAGE3_DIR, "stage3_progress.json")
DEEPSEEK_CONFIG_PATH = os.path.join(ACCOUNT_DIR, "deepseek_config.json")

# 可被外部覆盖的参数
DEBUG_MODE = False
DEBUG_ITEM_LIMIT = 5
CONCURRENCY_LIMIT = 1
MIN_VIDEO_DURATION_SECONDS = 60      # 最短视频时长（秒），小于此值跳过总结

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

# 模型选择阈值
HIGH_VALUE_THRESHOLD = 6.0
MODEL_SMALL = "deepseek-v4-flash"   # 替代 deepseek-chat
MODEL_LARGE = "deepseek-v4-pro"     # 替代 deepseek-reasoner
MAX_INPUT_CHARS = 40000

# ==================== 工具函数 ====================
def safe_save_json(data: dict, filepath: str):
    tmp_path = filepath + ".tmp"
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, filepath)

def load_progress() -> Dict[str, str]:
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def load_deepseek_config() -> Tuple[str, str]:
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
    base_url = config.get("base_url", "").strip() or DEEPSEEK_BASE_URL
    return api_key, base_url

def select_model(impact_score: float, text_length: int) -> str:
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

    truncated_text = text[:MAX_INPUT_CHARS]
    if len(text) > MAX_INPUT_CHARS:
        trunc_note = f"\n[注意：原字幕总长{len(text)}字，已截取前{MAX_INPUT_CHARS}字进行总结。]"
        truncated_text += trunc_note

    system_prompt = (
        "你是一个顶级的知识蒸馏专家，擅长从视频文稿中提取高密度认知内容。\n"
        "请严格遵守以下规则：\n"
        "1. 仅基于提供的字幕/文本内容，不要引入外部知识。\n"
        "2. 自然过滤掉明显的广告口播、赞助内容、求三连等非知识信息。\n"
        "3. 使用中文输出，保持专业且平实的语气。\n"
    )

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

async def call_deepseek(api_key: str, base_url: str, model: str,
                        system: str, user: str, max_tokens: int,
                        temperature: float = 0.3, retry: int = 3) -> Optional[dict]:
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
                response_format={"type": "json_object"}
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
                sleep_time = 10 * (attempt + 1)
                print(f"  [RATE] 触发限流，休眠 {sleep_time} 秒...")
                await asyncio.sleep(sleep_time)
            elif "context" in err_msg or "length" in err_msg:
                print(f"  [FATAL] 输入超长，跳过此视频。")
                return None
            else:
                await asyncio.sleep(3 * (attempt + 1))
    return None

async def process_summary(bvid: str, node: dict, progress_cache: dict,
                          semaphore: asyncio.Semaphore, start_time: float,
                          completed: list, lock: asyncio.Lock, total: int,
                          api_key: str, base_url: str):
    async with semaphore:
        # ===== 断点恢复检查 =====
        if bvid in progress_cache:
            return

        # ===== 视频时长过滤 =====
        metadata = node.get("metadata", {})
        duration_sec = metadata.get("duration", 0)  # 单位：秒
        try:
            duration_sec = int(duration_sec)
        except (ValueError, TypeError):
            duration_sec = 0

        if duration_sec < MIN_VIDEO_DURATION_SECONDS:
            print(f"  [SKIP] {bvid} 时长不足1分钟 ({duration_sec}秒)，忽略。")
            # 记录为已处理（避免重复检查）
            progress_cache[bvid] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            safe_save_json(progress_cache, PROGRESS_FILE)
            async with lock:
                completed[0] += 1
            return

        # ===== 字幕文件存在性检查 =====
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

        # ===== 提取元数据 =====
        title = metadata.get("title", "")
        desc = metadata.get("description", "")
        tags = metadata.get("tags", [])
        cif = node.get("cognitive_impact_factor", 5.0)
        if isinstance(cif, dict):
            impact_score = float(cif.get("impact_score", 5.0))
        else:
            impact_score = float(cif)
        try:
            impact_score = float(impact_score)
        except (ValueError, TypeError):
            impact_score = 5.0

        text_len = len(full_text)
        chosen_model = select_model(impact_score, text_len)
        print(f"\n[Stage3] 处理: {bvid} | {title[:20]}... "
              f"(时长:{duration_sec}s, 字数:{text_len}, CIF:{impact_score:.1f}, 模型:{chosen_model})")

        # 构建提示词并调用
        system_prompt, user_prompt, max_tok = build_prompt(
            full_text, title, desc, tags, text_len, chosen_model
        )
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

        # 输出最终知识颗粒
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

        # 更新进度
        progress_cache[bvid] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        safe_save_json(progress_cache, PROGRESS_FILE)

        # 进度报告
        async with lock:
            completed[0] += 1
            elapsed = time.time() - start_time
            avg = elapsed / completed[0] if completed[0] > 0 else 0
            remaining = (total - completed[0]) * avg
            pct = (completed[0] / total) * 100
            print(f"  [PROG] {completed[0]}/{total} ({pct:.1f}%) | 预计剩余: {remaining/60:.1f}分钟")

        # API 安全休眠
        await asyncio.sleep(random.uniform(5.0, 12.0))

async def main():
    api_key, base_url = load_deepseek_config()
    print(f"[CONFIG] DeepSeek base_url: {base_url}")

    INPUT_FILE = os.path.join(DATA_DIR, "stage1_enrich", "master_enriched.json")
    if not os.path.exists(INPUT_FILE):
        print(f"[ERROR] 找不到提纯表: {INPUT_FILE}")
        return

    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        master_enriched = json.load(f)

    progress_cache = load_progress()

    # 筛选待处理视频：跳过已完成、无字幕或字幕为空的，同时过滤掉短视频（在 process_summary 中处理）
    pending_items = []
    for bvid, node in master_enriched.items():
        if bvid in progress_cache:
            continue
        # 可以提前做一次时长过滤以减少后续检查，但为了日志清晰，我们仍保留函数内部检查；
        # 这里可以快速跳过无字幕的，避免无用遍历
        sub_path = os.path.join(SUBTITLES_DIR, f"{bvid}.json")
        if not os.path.exists(sub_path):
            # 无字幕文件，直接标记为跳过（可选）
            continue
        try:
            with open(sub_path, 'r', encoding='utf-8') as f:
                sub_json = json.load(f)
            if not sub_json.get("full_text", "").strip():
                continue
        except:
            continue
        pending_items.append((bvid, node))

    if DEBUG_MODE:
        pending_items = pending_items[:DEBUG_ITEM_LIMIT]

    if not pending_items:
        print("[INFO] 所有视频总结已完成或无待处理任务。")
        return

    print(f"\n[STAGE 3] 将处理 {len(pending_items)} 个视频（跳过时长<{MIN_VIDEO_DURATION_SECONDS}s的视频）")
    print(f"[MODEL] 标准: {MODEL_SMALL} | 增强: {MODEL_LARGE} | 阈值: {HIGH_VALUE_THRESHOLD}")

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