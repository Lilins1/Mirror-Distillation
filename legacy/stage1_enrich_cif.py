import os
import sys
import json
import random
import asyncio
import httpx
from datetime import datetime

# ==========================================
# 目录架构重构与全局配置
# ==========================================
DATA_DIR = "data"
ACCOUNT_DIR = os.path.join(DATA_DIR, "account")
STAGE1_DIR = os.path.join(DATA_DIR, "stage1_collector")
ENRICH_DIR = os.path.join(DATA_DIR, "stage1_enrich")

os.makedirs(ENRICH_DIR, exist_ok=True)

CREDENTIAL_PATH = os.path.join(ACCOUNT_DIR, "credential.json")
INPUT_FILE = os.path.join(STAGE1_DIR, "master_index.json") # 直接读全局账本
MASTER_ENRICHED_FILE = os.path.join(ENRICH_DIR, "master_enriched.json")
PROGRESS_FILE = os.path.join(ENRICH_DIR, "enrich_progress.json")

DEBUG_MODE = False        # 生产环境建议设为 False
DEBUG_ITEM_LIMIT = 5     
CONCURRENCY_LIMIT = 1    

# 【安全增强】动态 User-Agent 池
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0"
]
def get_random_ua(): return random.choice(USER_AGENTS)

author_relation_cache = {}

# ==========================================
# 工具函数：原子级安全保存，防止写入时中断导致文件损坏
# ==========================================
def safe_save_json(data, filepath):
    tmp_path = filepath + ".tmp"
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, filepath)

async def get_cookies_from_local():
    if not os.path.exists(CREDENTIAL_PATH):
        raise FileNotFoundError("未找到凭证，请先运行 stage1_collector.py")
    with open(CREDENTIAL_PATH, 'r', encoding='utf-8') as f:
        cred_data = json.load(f)
    return {
        "SESSDATA": cred_data.get('sessdata', ''),
        "bili_jct": cred_data.get('bili_jct', ''),
        "buvid3": cred_data.get('buvid3', '')
    }

def recalculate_cif(node):
    cif = node['cognitive_impact_factor'] 
    status = node['interaction_status']
    
    if status.get('is_liked'): cif += 1.0
    if status.get('is_followed'): cif += 2.0  
    if status.get('is_favorited'): cif += 2.0
    
    # 基于真实投币数量赋予权重 (1个币1.5，2个币满配3.0)
    coin_count = status.get('coin_count', 0)
    if coin_count > 0: cif += (1.5 * coin_count)
    return round(cif, 3)

async def enrich_video_node(client, bvid, node):
    """
    单节点提纯逻辑：恢复分离请求，保证缓存不污染互动数据
    """
    author_mid = node['metadata'].get('author_mid', 0)
    req_headers = client.headers.copy()
    req_headers["Referer"] = f"https://www.bilibili.com/video/{bvid}/"
    
    # ==========================================
    # 请求 1：基础元数据获取 (分类、描述、aid)
    # ==========================================
    view_url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    aid = None
    try:
        view_resp = await client.get(view_url, headers=req_headers, timeout=10.0)
        if view_resp.status_code == 200:
            res_json = view_resp.json()
            if res_json.get('code') == 0:
                v_data = res_json.get('data', {})
                aid = v_data.get('aid')
                node['metadata']['category'] = v_data.get('tname', '未知分类')
                node['metadata']['description'] = v_data.get('desc', '')
            else:
                print(f"  [WARN] {bvid} 元数据提取失败: {res_json.get('message')}")
    except Exception as e:
        print(f"  [ERROR] 请求 {bvid} 元数据异常: {e}")
        
    await asyncio.sleep(random.uniform(1.0, 2.5))
    
    # ==========================================
    # 请求 1.5：标签提取 (基于 aid)
    # ==========================================
    node['metadata']['tags'] = []
    if aid:
        tag_url = f"https://api.bilibili.com/x/tag/archive/tags?aid={aid}"
        try:
            tag_resp = await client.get(tag_url, headers=req_headers, timeout=5.0)
            if tag_resp.status_code == 200 and tag_resp.json().get('code') == 0:
                tags_data = tag_resp.json().get('data', [])
                node['metadata']['tags'] = [t.get('tag_name') for t in tags_data if t.get('tag_name')]
        except Exception:
            pass
        await asyncio.sleep(random.uniform(0.5, 1.2))

    # ==========================================
    # 请求 2：绝对精准的实时互动关系 (点赞、投币、收藏)
    # ==========================================
    rel_url = f"https://api.bilibili.com/x/web-interface/archive/relation?bvid={bvid}"
    try:
        rel_resp = await client.get(rel_url, headers=req_headers, timeout=10.0)
        if rel_resp.status_code == 200:
            rel_json = rel_resp.json()
            if rel_json.get('code') == 0:
                data = rel_json.get('data', {})
                node['interaction_status']['is_liked'] = bool(data.get('like', 0))
                node['interaction_status']['is_favorited'] = bool(data.get('favorite', 0))
                node['interaction_status']['coin_count'] = int(data.get('coin', 0))
            else:
                print(f"  [WARN] {bvid} 互动状态提取失败: {rel_json.get('message')}")
    except Exception as e:
        print(f"  [ERROR] 请求 {bvid} 互动状态异常: {e}")
        
    await asyncio.sleep(random.uniform(1.2, 2.5))

    # ==========================================
    # 请求 3：UP 主关注状态 (带缓存机制)
    # ==========================================
    if author_mid and author_mid != 0:
        if author_mid not in author_relation_cache:
            u_rel_url = f"https://api.bilibili.com/x/relation?fid={author_mid}"
            try:
                u_resp = await client.get(u_rel_url, headers=req_headers, timeout=10.0)
                u_json = u_resp.json()
                if u_json.get('code') == 0:
                    attr = u_json.get('data', {}).get('attribute', 0)
                    author_relation_cache[author_mid] = (attr in [2, 6])
                else:
                    author_relation_cache[author_mid] = False
            except Exception:
                author_relation_cache[author_mid] = False
            await asyncio.sleep(random.uniform(0.5, 1.2)) 
        node['interaction_status']['is_followed'] = author_relation_cache[author_mid]
    else:
        node['interaction_status']['is_followed'] = False

    # 汇总重新计算终极 CIF 分数
    node['cognitive_impact_factor'] = recalculate_cif(node)
    return node


async def main():
    if not os.path.exists(INPUT_FILE):
        print(f"[ERROR] 找不到基础拓扑表: {INPUT_FILE}")
        return

    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        master_index = json.load(f)

    # 1. 增量读取进度和高优库（实现断点续传）
    progress_cache = {}
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
            progress_cache = json.load(f)

    master_enriched = {}
    if os.path.exists(MASTER_ENRICHED_FILE):
        with open(MASTER_ENRICHED_FILE, 'r', encoding='utf-8') as f:
            master_enriched = json.load(f)

    cookies = await get_cookies_from_local()
    headers = {"User-Agent": get_random_ua()}

    # 过滤出未提取过的高优节点
    pending_items = [(bvid, node) for bvid, node in master_index.items() if bvid not in progress_cache]
    
    if DEBUG_MODE:
        pending_items = pending_items[:DEBUG_ITEM_LIMIT]
    
    total_pending = len(pending_items)
    if total_pending == 0:
        print("\n[INFO] 所有节点均已提纯完毕，暂无增量任务。")
        return

    print(f"\n[ENRICH] 开始为 {total_pending} 个认知节点进行全维度提纯 (支持增量中断保存)...")
    
    # 防风控策略配置
    continuous_count = 0
    pause_threshold = random.randint(150, 200)

    # 仅针对本次运行统计的高价值留存，用于最终导出备份
    current_run_high_value_links = {}

    try:
        async with httpx.AsyncClient(cookies=cookies, headers=headers, timeout=15.0) as client:
            # 取消 asyncio.gather，改用迭代式处理，每处理一个就即时保存
            for i, (bvid, node) in enumerate(pending_items, 1):
                if(i % 10 == 0):
                    print(f"[PROGRESS] {i}/{total_pending} 正在提纯: {bvid}...")
                
                # 提纯当前节点
                enriched_node = await enrich_video_node(client, bvid, node)
                
                if enriched_node:
                    # 1. 更新进度
                    progress_cache[bvid] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    # 2. 判断并存入高优库
                    if enriched_node['cognitive_impact_factor'] >= 1.0:
                        master_enriched[bvid] = enriched_node
                        current_run_high_value_links[bvid] = enriched_node
                    
                    # 3. 即时安全存盘，随时中断不丢失数据
                    safe_save_json(progress_cache, PROGRESS_FILE)
                    safe_save_json(master_enriched, MASTER_ENRICHED_FILE)

                # 防风控挂机机制：每处理 150-200 个，休息 55-80 分钟
                continuous_count += 1
                if continuous_count >= pause_threshold:
                    sleep_minutes = random.uniform(10, 20)
                    print(f"\n[SLEEP] 为防止风控，已连续处理 {continuous_count} 个节点。正在进入深度睡眠休息 {sleep_minutes:.2f} 分钟...")
                    
                    # 使用小步进的 sleep，让挂机期间依然可以被 Ctrl+C 中断
                    total_seconds = int(sleep_minutes * 60)
                    for remaining in range(total_seconds, 0, -10):
                        sys.stdout.write(f"\r[SLEEP] 距离唤醒还剩 {remaining} 秒... ")
                        sys.stdout.flush()
                        await asyncio.sleep(min(10, remaining))
                    
                    print("\n[RESUME] 休息结束，继续执行提纯任务...\n")
                    continuous_count = 0
                    pause_threshold = random.randint(150, 200) # 重新生成随机阈值

    except asyncio.CancelledError:
        print("\n[WARN] 收到强制中断信号 (Cancelled)！进度已自动保存。")
    except KeyboardInterrupt:
        print("\n[WARN] 任务被手动终止 (Ctrl+C)！进度已自动保存。")
    except Exception as e:
        print(f"\n[FATAL] 出现未预期的全局异常: {e}")
    finally:
        # 无论正常结束还是中断，把本次运行抓取到的高优增量作一个归档备份
        if current_run_high_value_links:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            archive_file = os.path.join(ENRICH_DIR, f"enriched_links_{timestamp}.json")
            safe_save_json(current_run_high_value_links, archive_file)
            print(f"\n[-] 本次提纯高价值增量存档 (CIF >= 1.0): {len(current_run_high_value_links)} 条已备份至 {archive_file}")
            
        print(f"\n[SUCCESS] 认知拓扑网提纯执行完毕（或安全中断）！")
        print(f"[IO] 提纯表主库已更新至: {MASTER_ENRICHED_FILE} (当前总计 {len(master_enriched)} 个高分节点)")


if __name__ == '__main__':
    if sys.platform == 'win32':
        os.system('chcp 65001')
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass # 在 main 内部已被捕捉处理