import os
import sys
import json
import random
import asyncio
import httpx
import qrcode
import time  # 用于计算时间
from datetime import datetime
from bilibili_api import Credential, video 

DATA_DIR = "data"
ACCOUNT_DIR = os.path.join(DATA_DIR, "account")
ENRICH_DIR = os.path.join(DATA_DIR, "stage1_enrich")
STAGE2_DIR = os.path.join(DATA_DIR, "stage2_subtitles")
SUBTITLES_DIR = os.path.join(STAGE2_DIR, "parsed_videos")
os.makedirs(STAGE2_DIR, exist_ok=True)
os.makedirs(SUBTITLES_DIR, exist_ok=True)

INPUT_FILE = os.path.join(ENRICH_DIR, "master_enriched.json")
PROGRESS_FILE = os.path.join(STAGE2_DIR, "stage2_progress.json")
GUEST_CREDENTIAL_PATH = os.path.join(ACCOUNT_DIR, "guest_credential.json")

DEBUG_MODE = False           
DEBUG_ITEM_LIMIT = 5         
CONCURRENCY_LIMIT = 1  
ENABLE_SPONSOR_BLOCK = True
SPONSOR_BLOCK_CATEGORIES = ["sponsor", "selfpromo", "interaction"]

# ==========================================
# 引入大号的 UA 伪装池 (防风控必备)
# ==========================================
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0"
]
def get_random_ua(): return random.choice(USER_AGENTS)

# ==========================================
# 权限隔离逻辑 (已完全替换为 Stage 1 的原生机制)
# ==========================================
async def raw_qr_login_guest():
    print("\n" + "="*50)
    print("[原生 Auth] 正在向 B 站请求【小号】安全登录凭证...")
    headers = {"User-Agent": get_random_ua()}
    
    async with httpx.AsyncClient(headers=headers, timeout=15.0) as client:
        # 先获取基础的 buvid3 (参考大号逻辑)
        init_resp = await client.get("https://www.bilibili.com")
        buvid3 = init_resp.cookies.get("buvid3", "")
        
        # 原生请求扫码接口
        resp = await client.get("https://passport.bilibili.com/x/passport-login/web/qrcode/generate")
        data = resp.json()['data']
        qr_url = data['url']
        qrcode_key = data['qrcode_key']
        
        # 在终端渲染二维码
        qr = qrcode.QRCode()
        qr.add_data(qr_url)
        qr.print_ascii(invert=True) 
        
        print("="*50)
        print("[AUTH 降级] 请打开手机 Bilibili App，扫描上方二维码登录小号！")
        print("="*50)
        
        # 轮询扫码状态
        while True:
            await asyncio.sleep(2)
            poll_resp = await client.get(f"https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key={qrcode_key}")
            poll_data = poll_resp.json()['data']
            code = poll_data['code']
            
            if code == 0:
                print("\n[AUTH] 小号扫码确认成功！")
                cookies = poll_resp.cookies
                return Credential(sessdata=cookies.get("SESSDATA"), bili_jct=cookies.get("bili_jct"), buvid3=buvid3)
            elif code == 86038:
                print("\n[AUTH 失败] 二维码已过期，请重新运行脚本。")
                sys.exit(1)
            elif code == 86090:
                print("[-] 手机已扫码，请在手机端点击确认登录...")

async def get_guest_cookies():
    if os.path.exists(GUEST_CREDENTIAL_PATH):
        try:
            with open(GUEST_CREDENTIAL_PATH, 'r', encoding='utf-8') as f:
                cred_data = json.load(f)
            if cred_data.get('sessdata'):
                cred = Credential(sessdata=cred_data.get('sessdata', ''), bili_jct=cred_data.get('bili_jct', ''), buvid3=cred_data.get('buvid3', ''))
                if await cred.check_valid(): return cred
        except Exception: pass
        
    cred = await raw_qr_login_guest()
    with open(GUEST_CREDENTIAL_PATH, 'w', encoding='utf-8') as f:
        json.dump({"sessdata": cred.sessdata, "bili_jct": cred.bili_jct, "buvid3": cred.buvid3}, f, indent=4)
    return cred

# ==========================================
# 工具函数
# ==========================================
def safe_save_json(data, filepath):
    tmp_path = filepath + ".tmp"
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, filepath)

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f: return json.load(f)
        except Exception: return {}
    return {}

# ==========================================
# SponsorBlock 广告清洗核心函数
# ==========================================
async def get_sponsor_segments(bvid: str) -> tuple[list, str, str]:
    if not ENABLE_SPONSOR_BLOCK:
        return [], "disabled", "SponsorBlock广告清洗功能已全局关闭"
    
    try:
        categories = ",".join(f'"{cat}"' for cat in SPONSOR_BLOCK_CATEGORIES)
        url = f"https://sponsor.ajay.app/api/skipSegments?videoID={bvid}&categories=[{categories}]"
        
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url)
            
            if resp.status_code == 200:
                segments = []
                for item in resp.json():
                    segments.append({
                        "start": round(item["segment"][0], 1),
                        "end": round(item["segment"][1], 1),
                        "category": item["category"],
                        "votes": item["votes"]
                    })
                
                if segments:
                    return segments, "success", f"发现{len(segments)}个广告片段"
                else:
                    return [], "no_segments", "API调用成功，该视频无匹配类别的广告标记"
            
            elif resp.status_code == 404:
                return [], "no_segments", "该视频未被SponsorBlock社区标记过"
            else:
                return [], "api_error", f"API返回错误状态码: {resp.status_code}"
    
    except httpx.TimeoutException:
        return [], "network_error", "请求超时（8秒）"
    except httpx.NetworkError:
        return [], "network_error", "网络连接失败（DNS解析/防火墙拦截）"
    except Exception as e:
        return [], "api_error", f"未知错误: {str(e)[:50]}"

def filter_subtitle_by_segments(subtitle_body: list, segments: list) -> list:
    if not segments:
        return subtitle_body
    
    filtered_body = []
    for item in subtitle_body:
        start = item.get("from", 0)
        end = item.get("to", 0)
        
        is_ad = False
        for seg in segments:
            if start >= seg["start"] and end <= seg["end"]:
                is_ad = True
                break
        
        if not is_ad:
            filtered_body.append(item)
    
    return filtered_body

# =========================================================
# 核心处理管线：AI总结 > 官方字幕 > 广告清洗 > 放弃
# =========================================================
async def process_node(cred: Credential, bvid: str, node: dict, progress_cache: dict, semaphore: asyncio.Semaphore, start_time: float, completed: list, progress_lock: asyncio.Lock, total: int):
    async with semaphore:
        if bvid in progress_cache: return
        
        print(f"\n[Stage 2] 正在解析: {bvid} | {node['metadata']['title'][:15]}...")
        
        v = video.Video(bvid=bvid, credential=cred)
        
        full_text = ""
        data_source = "none"
        has_data = False
        
        # 获取SponsorBlock完整状态信息
        sponsor_segments, sponsor_status, sponsor_message = await get_sponsor_segments(bvid)
        
        # 打印差异化状态日志
        if sponsor_status == "success":
            print(f"  [🧹 SponsorBlock] {sponsor_message}")
        elif sponsor_status == "no_segments":
            print(f"  [ℹ️ SponsorBlock] {sponsor_message}")
        else:
            print(f"  [⚠️ SponsorBlock] 调用失败: {sponsor_message}")
        
        await asyncio.sleep(random.uniform(2.0, 4.0))
        # 1：优先白嫖 B 站官方 AI 视频总结
        cid = None   # 提前声明，避免未定义
        try:
            info = await v.get_info()
            cid = info.get('cid')
            up_mid = info.get('owner', {}).get('mid')
            
            api_url = f"https://api.bilibili.com/x/web-interface/view/conclusion/get?bvid={bvid}&cid={cid}&up_mid={up_mid}"
            req = await cred.request("GET", api_url)
            
            if req and req.get('code') == 0:
                model_result = req.get('data', {}).get('model_result', {})
                if model_result and model_result.get('result_type') == 1:
                    summary_text = model_result.get('summary', '')
                    outlines = model_result.get('outline', [])
                    
                    ai_blocks = [f"【AI 核心总结】: {summary_text}\n"]
                    for outline in outlines:
                        ai_blocks.append(f"- {outline.get('title', '')}")
                        for part in outline.get('part_outline', []):
                            ai_blocks.append(f"  * {part.get('content', '')}")
                            
                    full_text = "\n".join(ai_blocks)
                    has_data = True
                    data_source = "bilibili_ai_summary"
                    print(f"  [⚡ 降维打击] 成功白嫖 B站官方 AI 总结！免下载字幕。")
        except Exception as e:
            pass 

        await asyncio.sleep(random.uniform(2.5, 5.0))
        # 2：如果没有 AI 总结，尝试获取官方字幕并过滤广告
        if not has_data and cid is not None:          # 确保 cid 已获取
            try:
                subs_list = await v.get_subtitle(cid)
                if subs_list and subs_list.get('subtitles'):
                    sub_url = subs_list['subtitles'][0]['subtitle_url']
                    if sub_url.startswith("//"): sub_url = "https:" + sub_url
                    
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        sub_resp = await client.get(sub_url)
                        if sub_resp.status_code == 200:
                            body = sub_resp.json().get('body', [])
                            # 执行广告字幕过滤
                            filtered_body = filter_subtitle_by_segments(body, sponsor_segments)
                            ad_removed_count = len(body) - len(filtered_body)
                            
                            text_blocks = [item.get('content', '').strip() for item in filtered_body]
                            full_text = "\n".join(text_blocks)
                            has_data = True
                            data_source = "bilibili_subtitle"
                            
                            if ad_removed_count > 0:
                                print(f"  [√] 成功提取官方字幕，已过滤 {ad_removed_count} 条广告字幕")
                            else:
                                print(f"  [√] 成功通过加密鉴权，提取到官方字幕。")
            except Exception as e:
                print(f"  [WARN] {bvid} 字幕提取失败 (可能无字幕或无AI总结)")

        # ----------------------------------------------------
        # 数据落地（新增完整SponsorBlock状态记录）
        # ----------------------------------------------------
        output_data = {
            "video_id": bvid,
            "metadata": node["metadata"],
            "cognitive_impact_factor": node["cognitive_impact_factor"],
            "processing_status": {
                "has_content": has_data,
                "content_source": data_source, 
                "word_count": len(full_text),
                "sponsor_block": {
                    "enabled": ENABLE_SPONSOR_BLOCK,
                    "status": sponsor_status,
                    "message": sponsor_message,
                    "ad_segments_found": len(sponsor_segments),
                    "ad_segments": sponsor_segments
                }
            },
            "full_text": full_text
        }
        
        out_filepath = os.path.join(SUBTITLES_DIR, f"{bvid}.json")
        safe_save_json(output_data, out_filepath)
        # 进度保存
        progress_cache[bvid] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        safe_save_json(progress_cache, PROGRESS_FILE)
        
        # 新增：计算并打印进度百分比与预计剩余时间
        async with progress_lock:
            completed[0] += 1
            elapsed = time.time() - start_time
            avg_time_per_item = elapsed / completed[0]
            remaining_items = total - completed[0]
            eta_seconds = avg_time_per_item * remaining_items
            eta_minutes = eta_seconds / 60
            progress_percent = (completed[0] / total) * 100
            
            print(f"  [PROGRESS] {completed[0]}/{total} ({progress_percent:.1f}%) | 已用时: {elapsed/60:.1f}分钟 | 预计剩余: {eta_minutes:.1f}分钟")
        
        # 深度防风控休眠
        delay = random.uniform(25.0, 45.0)
        print(f"  [SLEEP] 安全休眠 {delay:.1f} 秒...")
        await asyncio.sleep(delay)

async def _run_extraction():
    """字幕提取核心逻辑（无参数，直接使用模块级配置）"""
    if not os.path.exists(INPUT_FILE):
        print(f"[ERROR] 找不到提纯表: {INPUT_FILE}")
        return
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        master_enriched = json.load(f)
    progress_cache = load_progress()
    
    cred = await get_guest_cookies()
    
    # 过滤音乐类视频
    pending_items = []
    skip_count = 0
    for bvid, node in master_enriched.items():
        if bvid in progress_cache:
            continue
        tags = node.get("metadata", {}).get("tags", [])
        if any("音乐" in str(tag) for tag in tags):
            skip_count += 1
            continue
        pending_items.append((bvid, node))
    
    if skip_count > 0:
        print(f"\n[FILTER] 自动净化：已拦截并跳过 {skip_count} 个包含“音乐”标签的视频（未计入处理额度）。")
    
    if DEBUG_MODE:
        pending_items = pending_items[:DEBUG_ITEM_LIMIT]
    
    if not pending_items:
        print("\n[INFO] 所有高优视频均已处理完毕，暂无增量任务。")
        return
    
    print(f"\n[STAGE 2] 开始处理 {len(pending_items)} 个视频的多模态抽取...")
    print(f"[INFO] 已开启【AI总结优先】降维打击策略，最大化降低风控概率！")
    if ENABLE_SPONSOR_BLOCK:
        print(f"[INFO] 已开启 SponsorBlock 广告自动清洗功能，过滤类别: {SPONSOR_BLOCK_CATEGORIES}")
        print(f"[INFO] 状态说明: success=有广告 | no_segments=无广告 | api_error=调用失败 | network_error=网络问题")
    
    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
    start_time = time.time()
    completed = [0]
    progress_lock = asyncio.Lock()
    total_pending = len(pending_items)
    
    tasks = [
        process_node(cred, bvid, node, progress_cache, semaphore,
                     start_time, completed, progress_lock, total_pending)
        for bvid, node in pending_items
    ]
    await asyncio.gather(*tasks)
    
    total_elapsed = (time.time() - start_time) / 60
    print(f"\n[SUCCESS] 第 2 阶段抽取完毕！")
    print(f"[-] 输出目录: {SUBTITLES_DIR}/")
    print(f"[-] 总用时: {total_elapsed:.1f}分钟 | 平均每个视频用时: {total_elapsed/total_pending:.1f}分钟")


async def main():
    """串行/独立模式入口（保持原有接口不变）"""
    await _run_extraction()


async def main_parallel(done_event: asyncio.Event = None):
    """并行模式入口：执行提取任务，完成后设置事件通知 Stage3"""
    await _run_extraction()
    if done_event:
        done_event.set()

if __name__ == '__main__':
    if sys.platform == 'win32':
        os.system('chcp 65001')
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())