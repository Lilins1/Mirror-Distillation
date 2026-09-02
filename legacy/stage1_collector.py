import os
import sys
import json
import random
import time
import asyncio
import httpx
import qrcode
from datetime import datetime
from bilibili_api import Credential, sync

DATA_DIR = "data"
ACCOUNT_DIR = os.path.join(DATA_DIR, "account")
STAGE1_DIR = os.path.join(DATA_DIR, "stage1_collector")

os.makedirs(ACCOUNT_DIR, exist_ok=True)
os.makedirs(STAGE1_DIR, exist_ok=True)

CREDENTIAL_PATH = os.path.join(ACCOUNT_DIR, "credential.json")
MASTER_INDEX_FILE = os.path.join(STAGE1_DIR, "master_index.json")

DEBUG_MODE = True       
DEBUG_MAX_PAGES = 2     
PROD_MAX_PAGES = 100    

DEEP_SCAN_INTERVAL = 3 * 24 * 3600  

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0"
]
def get_random_ua(): return random.choice(USER_AGENTS)

# ==========================================
# 🚀 新增：跨平台异步弹窗函数
# ==========================================
def _show_msgbox():
    """跨平台弹窗函数（在独立线程运行，不阻塞主程序）"""
    import platform
    msg = "【Mirror 蒸馏后台管线】\n\n大号(主账号)历史记录采集凭证已过期/失效。\n请使用 Bilibili App 扫描刚刚弹出的二维码图片！\n\n（扫码确认后后台会自动恢复运行，您可以直接关闭此提示框和图片）"
    title = "⚠️ 需要扫码授权 (主账号)"
    
    if platform.system() == "Windows":
        import ctypes
        # 0x1000 = 置顶 (System Modal), 0x40 = 信息图标
        ctypes.windll.user32.MessageBoxW(0, msg, title, 0x1000 | 0x40)
    else:
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw() # 隐藏主窗口
            root.attributes('-topmost', True) # 置顶
            messagebox.showinfo(title, msg, parent=root)
            root.destroy()
        except:
            pass

async def raw_qr_login():
    print("\n" + "="*50)
    print("[原生 Auth] 正在向 B 站请求安全登录凭证...")
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
        
        # ==========================================
        # 🚀 新增：生成图片并跨平台弹窗提醒
        # ==========================================
        qr_img_path = None
        try:
            img = qr.make_image(fill_color="black", back_color="white")
            qr_img_path = os.path.join(ACCOUNT_DIR, "master_qr_temp.png")
            img.save(qr_img_path)
            
            import platform
            import subprocess
            system = platform.system()
            
            # 1. 调用系统默认看图软件打开二维码
            if system == "Windows":
                os.startfile(qr_img_path)
            elif system == "Darwin": # macOS
                subprocess.run(["open", qr_img_path])
            else: # Linux
                subprocess.run(["xdg-open", qr_img_path])
                
            # 2. 丢入后台执行弹窗，防止 MessageBox 阻塞当前 async 循环
            asyncio.get_event_loop().run_in_executor(None, _show_msgbox)
            
        except Exception as e:
            print(f"[WARN] 自动弹窗展示失败: {e}。请确保已执行 pip install pillow")
        # ==========================================
        
        print("="*50)
        print("[AUTH 降级] 请打开手机 Bilibili App，扫描上方二维码！")
        print("="*50)
        
        while True:
            await asyncio.sleep(2)
            poll_resp = await client.get(f"https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key={qrcode_key}")
            poll_data = poll_resp.json()['data']
            code = poll_data['code']
            
            if code == 0:
                print("\n[AUTH] 扫码确认成功！")
                
                # 扫码成功后，自动清理刚才生成的二维码图片
                if qr_img_path and os.path.exists(qr_img_path):
                    try:
                        os.remove(qr_img_path)
                    except:
                        pass
                        
                cookies = poll_resp.cookies
                return Credential(sessdata=cookies.get("SESSDATA"), bili_jct=cookies.get("bili_jct"), buvid3=buvid3)
            elif code == 86038:
                print("\n[AUTH 失败] 二维码已过期，请重新运行脚本。")
                sys.exit(1)
            elif code == 86090:
                print("[-] 手机已扫码，请在手机端点击确认登录...")

async def get_smart_credential():
    if os.path.exists(CREDENTIAL_PATH):
        try:
            with open(CREDENTIAL_PATH, 'r', encoding='utf-8') as f:
                cred_data = json.load(f)
            cred = Credential(sessdata=cred_data.get('sessdata', ''), bili_jct=cred_data.get('bili_jct', ''), buvid3=cred_data.get('buvid3', ''))
            is_valid = await cred.check_valid()
            if is_valid:
                return cred
        except Exception:
            pass
    cred = await raw_qr_login()
    with open(CREDENTIAL_PATH, 'w', encoding='utf-8') as f:
        json.dump({"sessdata": cred.sessdata, "bili_jct": cred.bili_jct, "buvid3": cred.buvid3}, f, indent=4)
    return cred

def calculate_base_cif(progress, duration):
    if duration <= 0: return 0.1
    if progress == -1: return 1.5 
    return round((progress / duration) * 1.5, 3)

async def build_cognitive_topology(cred, max_pages=10):
    print(f"\n[FETCH] 开始增量拉取历史记录...")
    
    master_data = {}
    last_run_time = 0
    if os.path.exists(MASTER_INDEX_FILE):
        last_run_time = os.path.getmtime(MASTER_INDEX_FILE)
        with open(MASTER_INDEX_FILE, 'r', encoding='utf-8') as f:
            master_data = json.load(f)
    
    time_gap = time.time() - last_run_time
    deep_scan_mode = time_gap > DEEP_SCAN_INTERVAL
    if deep_scan_mode:
        print("[INFO] 距离上次抓取超过 3 天，触发【深度重置扫描】模式，将无视断点深度回溯。")
    
    new_incremental_links = {}
    cookies = {"SESSDATA": cred.sessdata, "bili_jct": cred.bili_jct, "buvid3": cred.buvid3}
    headers = {"User-Agent": get_random_ua(), "Referer": "https://www.bilibili.com/"}
    
    async with httpx.AsyncClient(cookies=cookies, headers=headers, timeout=15.0) as client:
        max_cursor = 0
        view_at_cursor = 0
        overlap_count = 0 
        
        for page in range(1, max_pages + 1):
            page_success = False
            should_break_outer = False
            
            for retry_count in range(3):
                if retry_count > 0:
                    print(f"[FETCH] 正在重新抓取第 {page} 页 (第 {retry_count}/2 次重试)...")
                else:
                    print(f"[FETCH] 正在抓取第 {page} 页...")
                    
                try:
                    url = f"https://api.bilibili.com/x/web-interface/history/cursor?max={max_cursor}&view_at={view_at_cursor}&business=archive"
                    resp = await client.get(url)
                    res_json = resp.json()
                    
                    if res_json.get('code') != 0: 
                        should_break_outer = True; break
                        
                    history_list = res_json.get('data', {}).get('list', [])
                    if not history_list: 
                        should_break_outer = True; break
                        
                    for item in history_list:
                        if item.get('history', {}).get('business') != 'archive':
                            continue
                            
                        bvid = item['history'].get('bvid')
                        view_time = item.get('view_at', 0)
                        
                        if bvid in master_data and master_data[bvid]['metadata']['view_at'] >= view_time:
                            overlap_count += 1
                            if not deep_scan_mode and overlap_count >= 3:
                                print(f"\n[BREAKPOINT] 连续匹配到已知旧记录，断点衔接成功！停止历史回溯。")
                                should_break_outer = True
                                break
                            continue 
                        else:
                            overlap_count = 0 
                        
                        if not bvid: continue
                        progress_raw = item.get('progress') or item.get('history', {}).get('progress', 0)
                        progress = int(progress_raw) if progress_raw else 0
                        duration = item.get('duration', 0)
                        
                        new_incremental_links[bvid] = {
                            "video_id": bvid,
                            "metadata": {
                                "title": item.get('title', '未知'),
                                "duration": duration,      
                                "progress": progress,      
                                "view_at": view_time,
                                "author": item.get('author_name', ''),
                                "author_mid": item.get('author_mid') or item.get('history', {}).get('mid', 0),
                                "category": "",     
                                "description": "",
                                "tags": []   
                            },
                            "cognitive_impact_factor": calculate_base_cif(progress, duration),
                            "interaction_status": {"coin_count": 0, "is_favorited": False, "is_liked": False} 
                        }
                    
                    if should_break_outer: break
                    if overlap_count >= 3 and not deep_scan_mode: 
                        should_break_outer = True; break 
                    
                    cursor = res_json.get('data', {}).get('cursor', {})
                    max_cursor = cursor.get('max', 0)
                    view_at_cursor = cursor.get('view_at', 0)
                    
                    if max_cursor == 0: 
                        should_break_outer = True; break
                    
                    sleep_time = random.uniform(3.0, 5.5) 
                    print(f"[-] 为避免触发反爬风控，随机休眠 {sleep_time:.2f} 秒...")
                    await asyncio.sleep(sleep_time)
                    
                    page_success = True
                    break
                    
                except Exception as e:
                    print(f"[ERROR] 第 {page} 页异常: {e}")
                    if retry_count < 2:
                        backoff = 3 ** (retry_count + 1)
                        print(f"[-] 触发防阻断重试机制，休眠 {backoff} 秒后重试...")
                        await asyncio.sleep(backoff)

            if should_break_outer:
                break
            if not page_success:
                print(f"[FATAL] 第 {page} 页连续失败 3 次，停止抓取以保护系统。")
                break

    print(f"\n[SUCCESS] 增量抓取完毕！本次发现 {len(new_incremental_links)} 个全新观看动作。")
    return new_incremental_links, master_data

def save_incremental_data(new_links, master_data):
    if not new_links:
        print("[IO] 本次无新数据，无需更新。")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
    archive_file = os.path.join(STAGE1_DIR, f"index_links_{timestamp}.json")
    with open(archive_file, 'w', encoding='utf-8') as f:
        json.dump(new_links, f, ensure_ascii=False, indent=2)
        
    master_data.update(new_links)
    with open(MASTER_INDEX_FILE, 'w', encoding='utf-8') as f:
        json.dump(master_data, f, ensure_ascii=False, indent=2)
        
    print(f"[IO] 历史全集 (Master 库) 已更新，总收录节点: {len(master_data)}")

async def main():
    cred = await get_smart_credential()
    pages_to_fetch = DEBUG_MAX_PAGES if DEBUG_MODE else PROD_MAX_PAGES
    if DEBUG_MODE: print("[DEBUG] 当前处于调试模式，将限制抓取上限！")
        
    new_links, master_data = await build_cognitive_topology(cred, max_pages=pages_to_fetch)
    save_incremental_data(new_links, master_data)

if __name__ == '__main__':
    if sys.platform == 'win32':
        os.system('chcp 65001')
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sync(main())