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

# ==================== 调试/行为开关 ====================
DEBUG_MODE = False
DEBUG_ITEM_LIMIT = 5
CONCURRENCY_LIMIT = 1
MIN_VIDEO_DURATION_SECONDS = 60      # 最短视频时长（秒），小于此值跳过总结

# ==================== 分段总结配置 ====================
ENABLE_SEGMENTED_SUMMARY = True       # 是否启用分段总结（关闭后超长文本会被粗暴截断）
SEGMENT_CHUNK_SIZE = 40000              # 每段最大字符数（正常使用建议 40000）
MAX_INPUT_CHARS = 40000                 # 非分段模式下的截断长度（正常使用建议 40000）
POLL_INTERVAL = 30  

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_QUOTA_EXHAUSTED = False   # 全局余额耗尽标志

# 模型选择阈值
HIGH_VALUE_THRESHOLD = 20.0
MODEL_SMALL = "deepseek-v4-flash"   # 分段摘要使用的小模型
MODEL_LARGE = "deepseek-v4-pro"     # 最终融合或高价值视频使用的大模型

# ==================== 认知画像提取阈值 ====================
COGNITIVE_VALUE_THRESHOLD = 1.0 *  HIGH_VALUE_THRESHOLD   # 认知画像提取阈值

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
                 text_length: int, chosen_model: str,
                 impact_score: float = 5.0) -> Tuple[str, str, int]:
    """动态生成提示词，根据长度和认知价值决定是否提取认知画像"""
    # 判断是否启用认知提取
    try:
        log_len = math.log(text_length + 1)
    except ValueError:
        log_len = 0.0
    score_metric = impact_score * log_len
    enable_cognitive = (score_metric >= COGNITIVE_VALUE_THRESHOLD)

    # 确定总结模式和长度要求
    if text_length <= 1500:
        mode = "short_summary"
        length_guideline = "100-200字，只保留最核心的观点与结论。"
        max_tokens = 800
    elif text_length <= 8000:
        mode = "medium_summary"
        length_guideline = "300-500字，按逻辑分段，包含主要论点和关键细节。"
        max_tokens = 2000
    else:
        mode = "deep_structured"
        length_guideline = "不少于800字，采用结构化格式（如：背景-问题-分析-结论），充分保留知识颗粒度。"
        max_tokens = 5000

    # 文本截断（非分段模式下有效）
    truncated_text = text[:MAX_INPUT_CHARS]
    if len(text) > MAX_INPUT_CHARS:
        trunc_note = f"\n[注意：原字幕总长{len(text)}字，已截取前{MAX_INPUT_CHARS}字进行总结。]"
        truncated_text += trunc_note

    # 系统提示
    system_prompt = (
        "你是一个顶级的知识蒸馏专家与认知建模师。\n"
        "严格基于提供的字幕，完成以下任务：\n"
        "1. 提炼客观知识内容（常规总结）。\n"
    )
    if enable_cognitive:
        system_prompt += (
            "2. 提取说话者的认知特征时，必须遵循：\n"
            "   - 仅使用原文中明确体现的证据，严禁任何推测或编造。\n"
            "   - 描述必须客观、精准，避免使用主观形容词（如“激昂”“冷峻”）。若文本中无情绪词，应写“无明显情绪倾向”。\n"
            "   - 每个维度如果缺乏足够的直接证据，请严格填写 'insufficient_data'，不要根据标题或背景强行猜测。\n"
        )
    else:
        system_prompt += "2. 文本较短或认知信号弱，无需提取认知特征。\n"
    system_prompt += (
        "3. 过滤广告、求三连等无关内容。\n"
        "4. 使用中文输出，保持分析性语气。\n"
    )

    # 用户提示
    user_prompt = f"""
视频标题：{title}
视频简介：{description}
原始标签：{', '.join(tags) if tags else '无'}
字幕全文（已自动去除时间轴）：
---
{truncated_text}
---

请完成以下任务并**严格按JSON格式输出**（不要添加其他任何文字）：
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
"""
    if enable_cognitive:
        user_prompt += """,
  "cognitive_signal_strength": 1-10的整数，评估文本在多大程度上体现了说话者的个人认知风格（1=完全无法推断，10=极其明显）,
  "cognitive_profile": {{
    "language_style": "仅基于原文中的词汇、句式和修辞特点描述；若无法判断则填 'insufficient_data'",
    "thinking_mode": "仅基于原文的推理方式（如归纳、类比、层层递进等）描述；若无法判断则填 'insufficient_data'",
    "values_preferences": "仅基于原文中明确表达的偏好或原则描述；若无法判断则填 'insufficient_data'",
    "core_beliefs": "仅基于原文中反复出现的底层观点或世界观描述；若无法判断则填 'insufficient_data'",
    "argumentation_pattern": "仅基于原文的论证结构（如举例→结论、先破后立）描述；若无法判断则填 'insufficient_data'",
    "emotional_tone": "仅基于原文中出现的情绪词或语气描述（如愤怒、讽刺），避免主观推断；若无明显情绪则写 '无明显情绪倾向'，无法确定则填 'insufficient_data'",
    "knowledge_framework": "仅基于原文引用的学科、概念或模型描述；若无法判断则填 'insufficient_data'",
    "decision_pattern": "仅基于原文中明确给出的决策逻辑或建议方式描述；若无法判断则填 'insufficient_data'"
  }}
"""
    user_prompt += "\n}"

    # 为认知特征预留更多 token
    if enable_cognitive:
        max_tokens = max(max_tokens, 3000)

    return system_prompt, user_prompt, max_tokens

def split_long_text(text: str, max_chars: int) -> List[str]:
    """按段落分割长文本，保证每段不超过 max_chars，尽量保持段落完整"""
    paragraphs = text.split('\n')
    chunks = []
    current_chunk = ""
    for para in paragraphs:
        if len(current_chunk) + len(para) + 1 <= max_chars:
            current_chunk = f"{current_chunk}\n{para}" if current_chunk else para
        else:
            if current_chunk:
                chunks.append(current_chunk)
            if len(para) > max_chars:
                for i in range(0, len(para), max_chars):
                    chunks.append(para[i:i+max_chars])
                current_chunk = ""
            else:
                current_chunk = para
    if current_chunk:
        chunks.append(current_chunk)
    return chunks

async def call_deepseek(api_key: str, base_url: str, model: str,
                        system: str, user: str, max_tokens: int,
                        temperature: float = 0.3, retry: int = 3) -> Optional[dict]:
    global DEEPSEEK_QUOTA_EXHAUSTED
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
            # 检测余额耗尽特征
            if "402" in str(e) or "insufficient balance" in err_msg or "quota" in err_msg:
                print(f"  [FATAL] DeepSeek API 余额不足/额度耗尽: {e}")
                DEEPSEEK_QUOTA_EXHAUSTED = True
                return None
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
        duration_sec = metadata.get("duration", 0)
        try:
            duration_sec = int(duration_sec)
        except (ValueError, TypeError):
            duration_sec = 0

        if duration_sec < MIN_VIDEO_DURATION_SECONDS:
            print(f"  [SKIP] {bvid} 时长不足1分钟 ({duration_sec}秒)，忽略。")
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

        # ===== 决定是否启用分段总结 =====
        use_segmented = (ENABLE_SEGMENTED_SUMMARY and text_len > SEGMENT_CHUNK_SIZE)

        if use_segmented:
            score_metric = impact_score * math.log(text_len + 1) if text_len > 0 else 0
            print(f"\n[Stage3-SEG] 超长视频: {bvid} | {title[:20]}... "
                f"(时长:{duration_sec}s, 字数:{text_len}, CIF:{impact_score:.1f}, score_metric:{score_metric:.1f})")
            print(f"  [SEGMENT] 启用分段总结，每段上限 {SEGMENT_CHUNK_SIZE} 字符")

            # 1. 分段
            segments = split_long_text(full_text, SEGMENT_CHUNK_SIZE)
            print(f"  [SEGMENT] 共分为 {len(segments)} 段")

            # 2. 逐段摘要
            seg_summaries = []
            for i, seg in enumerate(segments):
                seg_sys = "你是一个知识摘要专家。请用中文总结以下视频字幕片段的核心内容，保留关键信息。"
                seg_user = f"""字幕片段：
---
{seg}
---
请严格按JSON格式输出（不要添加其他文字）：
{{
  "summary": "该片段的摘要内容，100-300字"
}}"""
                seg_max_tok = min(1500, max(400, int(len(seg) * 0.5)))
                print(f"  [SEGMENT] 处理第{i+1}/{len(segments)}段，长度{len(seg)}...")
                seg_result = await call_deepseek(api_key, base_url, MODEL_SMALL,
                                                 seg_sys, seg_user, seg_max_tok, temperature=0.3, retry=2)
                if seg_result and "summary" in seg_result:
                    seg_summaries.append(seg_result["summary"])
                else:
                    seg_summaries.append("[片段总结失败]")
                await asyncio.sleep(random.uniform(2.0, 5.0))

            merged_summary = "\n\n---\n\n".join(seg_summaries)

            # 3. 最终融合（动态加入认知提取）
            final_model = select_model(impact_score, len(merged_summary))
            # 根据原始视频参数判断是否提取认知
            try:
                log_len = math.log(text_len + 1)
            except ValueError:
                log_len = 0.0
            score_metric = impact_score * log_len
            final_enable_cognitive = (score_metric >= COGNITIVE_VALUE_THRESHOLD)

            final_sys = (
                "你是一个顶级的知识蒸馏专家与认知建模师。\n"
                "请基于提供的视频分段摘要，整合成一份完整的总结。\n"
            )
            if final_enable_cognitive:
                final_sys += (
                    "如果摘要中能反映说话者的思维特征，请提取认知画像。"
                    "必须客观、精准，仅基于摘要中的直接证据，不得推测。"
                    "每个维度若缺乏足够证据，必须填写 'insufficient_data'，严禁编造。\n"
                )
            else:
                final_sys += "由于原始信号弱，无需提取认知特征。\n"

            # 构建认知部分 JSON 片段
            cognitive_part = ""
            if final_enable_cognitive:
                cognitive_part = """,
  "cognitive_signal_strength": 1-10的整数，评估摘要集合在多大程度上体现了说话者的个人认知风格,
  "cognitive_profile": {
    "language_style": "描述语言风格；若无法推断则填 'insufficient_data'",
    "thinking_mode": "归纳思维方式；若无法推断则填 'insufficient_data'",
    "values_preferences": "价值观与偏好；若无法推断则填 'insufficient_data'",
    "core_beliefs": "底层信念；若无法推断则填 'insufficient_data'",
    "argumentation_pattern": "论证结构；若无法推断则填 'insufficient_data'",
    "emotional_tone": "情绪基调；若无法推断则填 'insufficient_data'",
    "knowledge_framework": "知识框架；若无法推断则填 'insufficient_data'",
    "decision_pattern": "决策模式；若无法推断则填 'insufficient_data'"
  }"""

            final_user = f"""
视频标题：{title}
视频简介：{desc}
原始标签：{', '.join(tags) if tags else '无'}

以下是该视频的分段摘要：
---
{merged_summary}
---

请完成以下任务并**严格按JSON格式输出**（不要添加其他文字）：
{{
  "mode": "segmented_deep_structured",
  "summary": "综合所有片段的完整结构化总结，不少于500字，采用结构化格式（如：背景-问题-分析-结论），充分保留知识颗粒度。",
  "tags": {{
    "primary_category": "一级分类，如：科技/人文/教育/娱乐/生活/商业/其他",
    "secondary_category": "二级分类（细化）",
    "keywords": ["关键词1", "关键词2", "关键词3", ...]
  }},
  "knowledge_value_score": 1-10的整数，表示本文的知识密度与价值，
  "is_ad_contaminated": true/false，若内容中混杂大量无关推广内容则标记为true
{cognitive_part}
}}"""
            ai_result = await call_deepseek(api_key, base_url, final_model,
                                            final_sys, final_user, 5000, temperature=0.3)
            if not ai_result:
                print("  [WARN] 最终融合失败，使用分段摘要拼接作为结果")
                ai_result = {
                    "mode": "segmented_fallback",
                    "summary": merged_summary,
                    "tags": {},
                    "knowledge_value_score": 0,
                    "is_ad_contaminated": False
                }
            chosen_model = final_model
        else:
            # ===== 常规单次总结（或未启用分段） =====
            chosen_model = select_model(impact_score, text_len)
            score_metric = impact_score * math.log(text_len + 1) if text_len > 0 else 0
            print(f"\n[Stage3] 处理: {bvid} | {title[:20]}... "
                f"(时长:{duration_sec}s, 字数:{text_len}, CIF:{impact_score:.1f}, score_metric:{score_metric:.1f}, 模型:{chosen_model})")

            system_prompt, user_prompt, max_tok = build_prompt(
                full_text, title, desc, tags, text_len, chosen_model,
                impact_score=impact_score
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

        # ===== 输出最终知识颗粒 =====
        ai_distillation = {
            "model": chosen_model,
            "timestamp": datetime.now().isoformat(),
            "mode": ai_result.get("mode", "unknown"),
            "summary": ai_result.get("summary", ""),
            "tags": ai_result.get("tags", {}),
            "knowledge_value_score": ai_result.get("knowledge_value_score", 0),
            "is_ad_contaminated": ai_result.get("is_ad_contaminated", False)
        }
        # 如果有认知数据，一并保存
        if "cognitive_signal_strength" in ai_result:
            ai_distillation["cognitive_signal_strength"] = ai_result["cognitive_signal_strength"]
        if "cognitive_profile" in ai_result:
            ai_distillation["cognitive_profile"] = ai_result["cognitive_profile"]

        knowledge_grain = {
            "video_id": bvid,
            "metadata": metadata,
            "cognitive_impact_factor": cif,
            "sponsor_block_info": sub_data.get("processing_status", {}).get("sponsor_block", {}),
            "ai_distillation": ai_distillation
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

    # 筛选待处理视频
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
    if ENABLE_SEGMENTED_SUMMARY:
        print(f"[SEGMENT] 分段总结已启用，分段上限: {SEGMENT_CHUNK_SIZE} 字符")
    else:
        print(f"[SEGMENT] 分段总结已关闭，超长文本将被截断至 {MAX_INPUT_CHARS} 字符")
    print(f"[COGNITIVE] 认知提取条件：综合评分度量 (score_metric) ≥ {COGNITIVE_VALUE_THRESHOLD}")

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


async def main_parallel(done_event: asyncio.Event = None):
    """并行模式入口：持续扫描新字幕并总结，直到生产者完成且无新任务"""
    api_key, base_url = load_deepseek_config()
    print(f"[CONFIG] DeepSeek base_url: {base_url}")

    INPUT_FILE = os.path.join(DATA_DIR, "stage1_enrich", "master_enriched.json")
    if not os.path.exists(INPUT_FILE):
        print(f"[ERROR] 找不到提纯表: {INPUT_FILE}")
        return

    progress_cache = load_progress()
    last_processed = set(progress_cache.keys())  # 本轮已处理，避免重复
    parallel_start = time.time()   # 记录开始时间

    while True:
        # 余额耗尽直接退出
        if DEEPSEEK_QUOTA_EXHAUSTED:
            print("[Stage3] 检测到余额耗尽，立即停止轮询。")
            break
        # 重新读取主表（支持运行中新增数据）
        with open(INPUT_FILE, 'r', encoding='utf-8') as f:
            master_enriched = json.load(f)

        # 筛选：有字幕且未总结的视频
        pending = []
        for bvid, node in master_enriched.items():
            if bvid in progress_cache or bvid in last_processed:
                continue
            sub_path = os.path.join(SUBTITLES_DIR, f"{bvid}.json")
            if not os.path.exists(sub_path):
                continue
            try:
                with open(sub_path, 'r', encoding='utf-8') as f:
                    sub_data = json.load(f)
                if not sub_data.get("full_text", "").strip():
                    continue
            except:
                continue
            pending.append((bvid, node))

        if DEBUG_MODE:
            pending = pending[:DEBUG_ITEM_LIMIT]

        if pending:
            print(f"\n[Stage3 轮询] 发现 {len(pending)} 个新字幕，开始批量总结...")
            semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
            start_time = time.time()
            completed = [0]
            lock = asyncio.Lock()
            tasks = [
                process_summary(bvid, node, progress_cache, semaphore,
                                start_time, completed, lock, len(pending),
                                api_key, base_url)
                for bvid, node in pending
            ]
            await asyncio.gather(*tasks)
            # 记录已处理
            for bvid, _ in pending:
                last_processed.add(bvid)
        else:
            # 无新任务，判断是否退出
            if done_event and done_event.is_set():
                print("[Stage3] 生产者已停止且无新任务，轮询结束。")
                break
            print(f"[Stage3] 暂无新字幕，{POLL_INTERVAL}秒后再次检查...")
            await asyncio.sleep(POLL_INTERVAL)

    total_elapsed = (time.time() - parallel_start) / 60
    print(f"[Stage3 并行模式] 退出。总用时: {total_elapsed:.1f}分钟")
    
if __name__ == '__main__':
    if sys.platform == 'win32':
        os.system('chcp 65001')
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())