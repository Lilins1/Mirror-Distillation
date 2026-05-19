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

DATA_DIR = "data"
ACCOUNT_DIR = os.path.join(DATA_DIR, "account")
ENRICH_DIR = os.path.join(DATA_DIR, "stage1_enrich")
STAGE2_DIR = os.path.join(DATA_DIR, "stage2_subtitles")
SUBTITLES_DIR = os.path.join(STAGE2_DIR, "parsed_videos")

os.makedirs(STAGE2_DIR, exist_ok=True)
os.makedirs(SUBTITLES_DIR, exist_ok=True)

# 【核心修复】直接读取 Stage 1.5 的全局高分账本
INPUT_FILE = os.path.join(ENRICH_DIR, "master_enriched.json")
PROGRESS_FILE = os.path.join(STAGE2_DIR, "stage2_progress.json")
GUEST_CREDENTIAL_PATH = os.path.join(ACCOUNT_DIR, "guest_credential.json")

DEBUG_MODE = False           
DEBUG_ITEM_LIMIT = 5         
CONCURRENCY_LIMIT = 2        

SKIP_CATEGORIES = ["sponsor", "intro", "outro", "interaction"]

ENABLE_LOCAL_WHISPER = False 
WHISPER_MODEL_SIZE = "small" 

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0"
]
def get_random_ua(): return random.choice(USER_AGENTS)

async def raw_qr_login_guest():
    print("\n" + "="*50)
    print("[权限隔离] Stage 2 正在请求【小号/抓取专用号】安全凭证...")
    headers = {"User-Agent": get_random_ua()}
    
    async with httpx.AsyncClient(headers=headers, timeout=15.0) as client:
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
            await asyncio.sleep(random.uniform(2.5, 10))
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
    if os.path.exists(GUEST_CREDENTIAL_PATH):
        try:
            with open(GUEST_CREDENTIAL_PATH, 'r', encoding='utf-8') as f:
                cred_data = json.load(f)
            if cred_data.get('sessdata'):
                return {
                    "SESSDATA": cred_data.get('sessdata', ''),
                    "bili_jct": cred_data.get('bili_jct', ''),
                    "buvid3": cred_data.get('buvid3', '')
                }
        except Exception:
            pass
            
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
        # 【安全增强】外网 SponsorBlock 极速 3.0 秒熔断防卡死
        resp = await client.get(url, timeout=3.0)
        if resp.status_code == 200:
            for item in resp.json():
                segment = item.get("segment")
                if segment and len(segment) == 2:
                    ad_segments.append(segment)
    except httpx.TimeoutException:
        print(f"[WARN] 提取 {bvid} 广告切片超时 (SponsorBlock 连通性差，已安全放行)")
    except Exception as e:
        print(f"[WARN] 提取 {bvid} 广告切片跳过 (可能无赞助): {e}")
    return ad_segments

def is_segment_in_ad(start_time, end_time, ad_segments):
    for ad_start, ad_end in ad_segments:
        if max(start_time, ad_start) < min(end_time, ad_end): return True
    return False

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

    manual_subs = []
    ai_subs = []
    for s in subs:
        if s.get('ai_type') == 1 or '自动' in s.get('lan_doc', ''):
            ai_subs.append(s)
        else:
            manual_subs.append(s)

    target_sub = None
    sub_source = "none"

    if manual_subs:
        target_sub = manual_subs[0]
        sub_source = "bilibili_cc_human"
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
    return await loop.run_in_executor(None, run_whisper_sync, bvid)

async def process_single_video(client, bvid, node, progress_cache, semaphore):
    async with semaphore:
        if bvid in progress_cache: return None
            
        print(f"\n[-] 正在解析节点: {bvid} | {node['metadata']['title'][:20]}...")
        req_headers = client.headers.copy()
        req_headers["Referer"] = f"https://www.bilibili.com/video/{bvid}/"
        
        raw_subs, sub_source = await fetch_bilibili_subtitles(client, bvid, req_headers)
        
        if not raw_subs and ENABLE_LOCAL_WHISPER:
            raw_subs = await fetch_local_whisper_asr(bvid)
            if raw_subs:
                sub_source = "local_whisper_asr"
        
        has_subtitles = len(raw_subs) > 0
        cleaned_text_blocks = []
        ad_segments = []
        
        if has_subtitles:
            ad_segments = await fetch_ad_segments(client, bvid)
            for item in raw_subs:
                start_t = item.get('from', 0)
                end_t = item.get('to', 0)
                content = item.get('content', '').strip()
                
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
                "subtitle_source": sub_source, 
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
        master_enriched = json.load(f)

    progress_cache = {}
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
            progress_cache = json.load(f)

    cookies = await get_guest_cookies()
    headers = {"User-Agent": get_random_ua()}

    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
    
    # 【核心断点逻辑】过滤出未提取过字幕的高优节点
    pending_items = [(bvid, node) for bvid, node in master_enriched.items() if bvid not in progress_cache]
    if DEBUG_MODE: pending_items = pending_items[:DEBUG_ITEM_LIMIT]

    if not pending_items:
        print("\n[INFO] 所有高优视频字幕均已提取完毕，暂无增量任务。")
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