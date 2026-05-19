import os
import sys
import json
import random
import asyncio
import httpx
import tempfile
from datetime import datetime
import qrcode
from bilibili_api import Credential

# ==========================================
# 目录架构重构与全局配置
# ==========================================
DATA_DIR = "data"
ACCOUNT_DIR = os.path.join(DATA_DIR, "account")
ENRICH_DIR = os.path.join(DATA_DIR, "stage1_enrich")
STAGE2_DIR = os.path.join(DATA_DIR, "stage2_subtitles")
SUBTITLES_DIR = os.path.join(STAGE2_DIR, "parsed_videos")

os.makedirs(STAGE2_DIR, exist_ok=True)
os.makedirs(SUBTITLES_DIR, exist_ok=True)

INPUT_FILE = os.path.join(ENRICH_DIR, "enriched_links_latest.json")
PROGRESS_FILE = os.path.join(STAGE2_DIR, "stage2_progress.json")
GUEST_CREDENTIAL_PATH = os.path.join(ACCOUNT_DIR, "guest_credential.json")

DEBUG_MODE = False           
DEBUG_ITEM_LIMIT = 5         
CONCURRENCY_LIMIT = 2        

SKIP_CATEGORIES = ["sponsor", "intro", "outro", "interaction"]

# ==========================================
# AI 语言识别兜底配置 (ASR Fallback)
# ==========================================
# 如果开启，当B站人工字幕和AI字幕都失效时，将下载音频并在本地使用 GPU/CPU 强行听写
ENABLE_LOCAL_WHISPER = False 
WHISPER_MODEL_SIZE = "small" # 可选: tiny, base, small, medium, large-v3

async def raw_qr_login_guest():
    print("\n" + "="*50)
    print("[权限隔离] Stage 2 正在请求【小号/抓取专用号】安全凭证...")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    async with httpx.AsyncClient(headers=headers) as client:
        init_resp = await client.get("https://www.bilibili.com")
        buvid3 = init_resp.cookies.get("buvid3", "")
        
        resp = await client.get("https://passport.bilibili.com/x/passport-login/web/qrcode/generate")
        data = resp.json()['data']
        qr_url = data['url']
        qrcode_key = data['qrcode_key']
        
        qr = qrcode.QRCode()
        qr.add_data(qr_url)
        qr.print_ascii(invert=True) 
        
        print("="*50)
        print("⚠️ [安全提示] 为了保护你的大号安全，请使用【B站小号】扫描上方二维码！")
        print("="*50)
        
        while True:
            await asyncio.sleep(2)
            poll_resp = await client.get(f"https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key={qrcode_key}")
            poll_data = poll_resp.json()['data']
            code = poll_data['code']
            
            if code == 0:
                print("\n[AUTH] 小号扫码确认成功！已实现风险隔离。")
                cookies = poll_resp.cookies
                return Credential(sessdata=cookies.get("SESSDATA"), bili_jct=cookies.get("bili_jct"), buvid3=buvid3)
            elif code == 86038:
                print("\n[AUTH 失败] 二维码已过期，请重新运行。")
                sys.exit(1)
            elif code == 86090:
                print("[-] 手机已扫码，请在手机端点击确认登录...")

async def get_guest_cookies():
    """获取小号/游客 Cookie，实现读写权限分离"""
    if os.path.exists(GUEST_CREDENTIAL_PATH):
        try:
            with open(GUEST_CREDENTIAL_PATH, 'r', encoding='utf-8') as f:
                cred_data = json.load(f)
            # 简单验证一下凭证是否存在
            if cred_data.get('sessdata'):
                return {
                    "SESSDATA": cred_data.get('sessdata', ''),
                    "bili_jct": cred_data.get('bili_jct', ''),
                    "buvid3": cred_data.get('buvid3', '')
                }
        except Exception:
            pass
            
    # 如果没有找到小号凭证，触发扫码并保存
    cred = await raw_qr_login_guest()
    with open(GUEST_CREDENTIAL_PATH, 'w', encoding='utf-8') as f:
        json.dump({
            "sessdata": cred.sessdata, 
            "bili_jct": cred.bili_jct, 
            "buvid3": cred.buvid3
        }, f, indent=4)
        
    return {
        "SESSDATA": cred.sessdata,
        "bili_jct": cred.bili_jct,
        "buvid3": cred.buvid3
    }

async def fetch_ad_segments(client: httpx.AsyncClient, bvid: str):
    categories_param = "[" + ",".join('"' + c + '"' for c in SKIP_CATEGORIES) + "]"
    url = f"https://sponsor.ajay.app/api/skipSegments?videoID={bvid}&categories={categories_param}"
    ad_segments = []
    try:
        resp = await client.get(url, timeout=10.0)
        if resp.status_code == 200:
            for item in resp.json():
                segment = item.get("segment")
                if segment and len(segment) == 2:
                    ad_segments.append(segment)
    except Exception as e:
        print(f"[WARN] 提取 {bvid} 广告切片跳过 (可能无赞助): {e}")
    return ad_segments

def is_segment_in_ad(start_time, end_time, ad_segments):
    for ad_start, ad_end in ad_segments:
        if max(start_time, ad_start) < min(end_time, ad_end): return True
    return False

# ====================================================
# [双重兜底 1] B站原生人工字幕 / B站官方AI字幕提取
# ====================================================
async def fetch_bilibili_subtitles(client: httpx.AsyncClient, bvid: str, req_headers: dict):
    view_url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    cid = None
    try:
        view_resp = await client.get(view_url, headers=req_headers)
        if view_resp.status_code == 200:
            cid = view_resp.json().get('data', {}).get('cid')
    except Exception: return [], "none"

    if not cid: return [], "none"
    await asyncio.sleep(random.uniform(0.5, 1.2))
    
    player_url = f"https://api.bilibili.com/x/player/v2?bvid={bvid}&cid={cid}"
    subs = []
    try:
        player_resp = await client.get(player_url, headers=req_headers)
        if player_resp.status_code == 200:
            subs = player_resp.json().get('data', {}).get('subtitle', {}).get('subtitles', [])
    except Exception: pass

    if not subs: return [], "none"

    # 分类：区分 UP主人工字幕 和 B站官方自动生成的 AI 字幕
    manual_subs = []
    ai_subs = []
    for s in subs:
        if s.get('ai_type') == 1 or '自动' in s.get('lan_doc', ''):
            ai_subs.append(s)
        else:
            manual_subs.append(s)

    target_sub = None
    sub_source = "none"

    # 优先级 1: 人工中文字幕 > 人工其他语言
    if manual_subs:
        target_sub = manual_subs[0]
        sub_source = "bilibili_cc_human"
    # 优先级 2 (兜底): B站官方 AI 自动字幕
    elif ai_subs:
        target_sub = ai_subs[0]
        sub_source = "bilibili_ai_auto"
        print(f"[-] {bvid} 未发现人工字幕，触发【B站官方 AI 字幕】降级。")

    if not target_sub: return [], "none"

    sub_url = target_sub.get('subtitle_url')
    if sub_url and sub_url.startswith("//"): sub_url = "https:" + sub_url
        
    try:
        sub_content_resp = await client.get(sub_url, headers=req_headers)
        if sub_content_resp.status_code == 200:
            return sub_content_resp.json().get('body', []), sub_source
    except Exception: pass
        
    return [], "none"

# ====================================================
# [双重兜底 2] 本地 Faster-Whisper 大模型极限转写
# ====================================================
def run_whisper_sync(bvid):
    try:
        import yt_dlp
        from faster_whisper import WhisperModel
    except ImportError:
        print(f"\n[ERROR] 缺少本地 ASR 依赖。请安装: pip install yt-dlp faster-whisper")
        return []

    print(f"[!] {bvid} 触发极限兜底: 唤醒本地 Whisper ASR 提取音频并转写...")
    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, f"{bvid}.m4a")
        ydl_opts = {
            'format': 'm4a/bestaudio/best',
            'outtmpl': audio_path,
            'quiet': True,
            'no_warnings': True,
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([f"https://www.bilibili.com/video/{bvid}"])
        except Exception as e:
            print(f"[ERROR] yt-dlp 下载音频失败: {e}")
            return []

        try:
            # 自动探测硬件加速 (CUDA GPU 或 CPU 回退)
            model = WhisperModel(WHISPER_MODEL_SIZE, device="auto", compute_type="default")
            segments, _ = model.transcribe(audio_path, beam_size=5, language="zh")

            raw_subs = []
            for segment in segments:
                raw_subs.append({
                    "from": segment.start,
                    "to": segment.end,
                    "content": segment.text.strip()
                })
            print(f"[!] {bvid} 本地 Whisper 听写完毕！")
            return raw_subs
        except Exception as e:
            print(f"[ERROR] Whisper 转写彻底失败: {e}")
            return []

async def fetch_local_whisper_asr(bvid: str):
    loop = asyncio.get_event_loop()
    # 丢入默认的线程池执行阻塞任务，防止卡死 asyncio 循环
    return await loop.run_in_executor(None, run_whisper_sync, bvid)

# ====================================================
# 主流程管线
# ====================================================
async def process_single_video(client, bvid, node, progress_cache, semaphore):
    async with semaphore:
        if bvid in progress_cache: return None
            
        print(f"\n[-] 正在解析节点: {bvid} | {node['metadata']['title'][:20]}...")
        req_headers = client.headers.copy()
        req_headers["Referer"] = f"https://www.bilibili.com/video/{bvid}/"
        
        # 1. 第一阶段获取：请求原生字幕与B站AI字幕
        raw_subs, sub_source = await fetch_bilibili_subtitles(client, bvid, req_headers)
        
        # 2. 极限兜底：如果前两个都没有，且开启了本地识别
        if not raw_subs and ENABLE_LOCAL_WHISPER:
            raw_subs = await fetch_local_whisper_asr(bvid)
            if raw_subs:
                sub_source = "local_whisper_asr"
        
        has_subtitles = len(raw_subs) > 0
        cleaned_text_blocks = []
        ad_segments = []
        
        if has_subtitles:
            # 向 SponsorBlock 索取恰饭广告节点
            ad_segments = await fetch_ad_segments(client, bvid)
            for item in raw_subs:
                start_t = item.get('from', 0)
                end_t = item.get('to', 0)
                content = item.get('content', '').strip()
                
                # 时间轴清洗，剔除恰饭文案
                if not is_segment_in_ad(start_t, end_t, ad_segments):
                    cleaned_text_blocks.append(content)
            await asyncio.sleep(random.uniform(1.0, 2.5))
        else:
            print(f"[WARN] {bvid} 所有字幕提取手段均失效 (可能为无声音频或被封锁)。")
        
        full_text = "\n".join(cleaned_text_blocks)
        word_count = len(full_text)
        
        output_data = {
            "video_id": bvid,
            "metadata": node["metadata"],
            "cognitive_impact_factor": node["cognitive_impact_factor"],
            "processing_status": {
                "has_subtitles": has_subtitles,
                "subtitle_source": sub_source,  # "bilibili_cc_human", "bilibili_ai_auto", "local_whisper_asr" 或 "none"
                "is_ad_filtered": len(ad_segments) > 0,
                "ad_segments_skipped": ad_segments,
                "subtitle_word_count": word_count 
            },
            "full_text": full_text
        }
        
        out_filepath = os.path.join(SUBTITLES_DIR, f"{bvid}.json")
        with open(out_filepath, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
            
        return bvid

async def main():
    if not os.path.exists(INPUT_FILE):
        print(f"[ERROR] 找不到提纯表: {INPUT_FILE}")
        return

    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        enriched_links = json.load(f)

    progress_cache = {}
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
            progress_cache = json.load(f)

    cookies = await get_guest_cookies()
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
    items_to_process = list(enriched_links.items())
    
    pending_items = [(bvid, node) for bvid, node in items_to_process if bvid not in progress_cache]
    if DEBUG_MODE: pending_items = pending_items[:DEBUG_ITEM_LIMIT]

    if not pending_items:
        print("\n[INFO] 所有视频字幕均已提取完毕，暂无增量任务。")
        return

    print(f"\n[STAGE 2] 开始处理 {len(pending_items)} 个视频的多模态抽取与清洗...")
    if ENABLE_LOCAL_WHISPER:
        print("[INFO] 本地 Whisper ASR 兜底引擎已就绪 (如果触发将调用 CPU/GPU 算力)")
    
    async with httpx.AsyncClient(cookies=cookies, headers=headers, timeout=15.0) as client:
        tasks = [process_single_video(client, bvid, node, progress_cache, semaphore) 
                 for bvid, node in pending_items]
        results = await asyncio.gather(*tasks)
        
    newly_processed = 0
    for res_bvid in results:
        if res_bvid:
            progress_cache[res_bvid] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            newly_processed += 1
            
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump(progress_cache, f, ensure_ascii=False, indent=2)

    print(f"\n[SUCCESS] 第 2 阶段抽取完毕！成功处理节点: {newly_processed}")
    print(f"[-] 输出目录: {SUBTITLES_DIR}/")

if __name__ == '__main__':
    if sys.platform == 'win32':
        os.system('chcp 65001')
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())