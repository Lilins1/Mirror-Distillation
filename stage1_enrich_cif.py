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
INPUT_FILE = os.path.join(STAGE1_DIR, "index_links_latest.json")

DEBUG_MODE = True        
DEBUG_ITEM_LIMIT = 5     
CONCURRENCY_LIMIT = 1    

author_relation_cache = {}

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
    
    coin_count = status.get('coin_count', 0)
    if coin_count > 0: cif += (1.5 * coin_count)
    return round(cif, 3)

async def fetch_video_tags_and_metadata(client: httpx.AsyncClient, bvid: str, req_headers: dict):
    metadata_result = {"category": "未知分类", "description": "", "tags": []}
    view_url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    aid = None
    try:
        view_resp = await client.get(view_url, headers=req_headers)
        if view_resp.status_code == 200:
            try:
                view_json = view_resp.json()
                if view_json.get('code') == 0:
                    v_data = view_json.get('data', {})
                    aid = v_data.get('aid')
                    metadata_result["category"] = v_data.get('tname', '未知分类')
                    metadata_result["description"] = v_data.get('desc', '')
            except Exception:
                print(f"[WARN] {bvid} 元数据接口触发风控拦截 (收到非JSON网页)")
        else:
            print(f"[WARN] {bvid} 元数据被拒绝: HTTP {view_resp.status_code}")
    except Exception as e:
        print(f"[ERROR] 请求 {bvid} 元数据失败: {e}")
        
    await asyncio.sleep(random.uniform(0.8, 1.8))
    
    if aid:
        tag_url = f"https://api.bilibili.com/x/tag/archive/tags?aid={aid}"
        try:
            tag_resp = await client.get(tag_url, headers=req_headers)
            if tag_resp.status_code == 200:
                try:
                    tag_json = tag_resp.json()
                    if tag_json.get('code') == 0:
                        tags_data = tag_json.get('data', [])
                        metadata_result["tags"] = [t.get('tag_name') for t in tags_data if t.get('tag_name')]
                except Exception:
                    print(f"[WARN] {bvid} 标签接口触发风控拦截")
            else:
                print(f"[WARN] {bvid} 标签被拒绝: HTTP {tag_resp.status_code}")
        except Exception as e:
            print(f"[ERROR] 请求 {bvid} 标签失败: {e}")
            
    return metadata_result

async def fetch_video_metadata_and_relation(client: httpx.AsyncClient, bvid: str, req_headers: dict):
    # 结果容器
    result = {
        "aid": None,
        "category": "未知分类", 
        "description": "", 
        "tags": [],
        "is_liked": False,
        "is_favorited": False,
        "coin_count": 0
    }
    
    # 核心优化：使用综合详情接口，一次性提取视图与用户鉴权互动状态
    detail_url = f"https://api.bilibili.com/x/web-interface/view/detail?bvid={bvid}"
    try:
        resp = await client.get(detail_url, headers=req_headers, timeout=10.0)
        if resp.status_code == 200:
            res_json = resp.json()
            if res_json.get('code') == 0:
                data = res_json.get('data', {})
                view_info = data.get('View', {})
                req_user = data.get('ReqUser', {})
                
                # 提取基础视图
                result["aid"] = view_info.get('aid')
                result["category"] = view_info.get('tname', '未知分类')
                result["description"] = view_info.get('desc', '')
                
                # 提取用户互动
                if req_user:
                    result["is_liked"] = bool(req_user.get('like', 0))
                    result["is_favorited"] = bool(req_user.get('favorite', 0))
                    result["coin_count"] = req_user.get('coin', 0)
            else:
                print(f"[WARN] {bvid} 详情接口返回错误: {res_json.get('message')}")
    except Exception as e:
        print(f"[ERROR] 请求 {bvid} 详情失败: {e}")
        
    await asyncio.sleep(random.uniform(1.2, 2.5))
    
    # 标签提取 (必须依赖 aid，所以保持独立请求)
    if result["aid"]:
        tag_url = f"https://api.bilibili.com/x/tag/archive/tags?aid={result['aid']}"
        try:
            tag_resp = await client.get(tag_url, headers=req_headers, timeout=5.0)
            if tag_resp.status_code == 200 and tag_resp.json().get('code') == 0:
                tags_data = tag_resp.json().get('data', [])
                result["tags"] = [t.get('tag_name') for t in tags_data if t.get('tag_name')]
        except Exception:
            pass
            
    return result

async def enrich_video_node(client, bvid, node, semaphore):
    async with semaphore:
        author_mid = node['metadata'].get('author_mid', 0)
        req_headers = client.headers.copy()
        req_headers["Referer"] = f"https://www.bilibili.com/video/{bvid}/"
        
        # 1. 执行优化后的合并请求
        extracted_data = await fetch_video_metadata_and_relation(client, bvid, req_headers)
        
        # 2. 赋值元数据
        node['metadata']['category'] = extracted_data["category"]
        node['metadata']['description'] = extracted_data["description"]
        node['metadata']['tags'] = extracted_data["tags"]
        
        # 3. 赋值互动状态
        node['interaction_status']['is_liked'] = extracted_data["is_liked"]
        node['interaction_status']['is_favorited'] = extracted_data["is_favorited"]
        node['interaction_status']['coin_count'] = extracted_data["coin_count"]
            
        await asyncio.sleep(random.uniform(1.0, 2.0))

        # 4. UP 主关注状态查询 (不变)
        if author_mid and author_mid != 0:
            if author_mid not in author_relation_cache:
                u_rel_url = f"https://api.bilibili.com/x/relation?fid={author_mid}"
                try:
                    rel_resp = await client.get(u_rel_url)
                    rel_data = rel_resp.json()
                    if rel_data.get('code') == 0:
                        attr = rel_data.get('data', {}).get('attribute', 0)
                        author_relation_cache[author_mid] = (attr in [2, 6])
                    else:
                        author_relation_cache[author_mid] = False
                except Exception:
                    author_relation_cache[author_mid] = False
                await asyncio.sleep(random.uniform(0.5, 1.2)) 
            node['interaction_status']['is_followed'] = author_relation_cache[author_mid]
        else:
            node['interaction_status']['is_followed'] = False

        node['cognitive_impact_factor'] = recalculate_cif(node)
        return node

async def main():
    if not os.path.exists(INPUT_FILE):
        print(f"[ERROR] 找不到基础拓扑表: {INPUT_FILE}")
        return

    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        cognitive_links = json.load(f)

    cookies = await get_cookies_from_local()
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
    items_to_process = list(cognitive_links.items())
    if DEBUG_MODE:
        items_to_process = items_to_process[:DEBUG_ITEM_LIMIT]
    
    print(f"\n[ENRICH] 开始为 {len(items_to_process)} 个认知节点进行全维度提纯...")
    
    async with httpx.AsyncClient(cookies=cookies, headers=headers) as client:
        tasks = [enrich_video_node(client, bvid, node, semaphore) for bvid, node in items_to_process]
        enriched_nodes = await asyncio.gather(*tasks)
        
    high_value_links = {node['video_id']: node for node in enriched_nodes if node['cognitive_impact_factor'] >= 1.0}

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_file = os.path.join(ENRICH_DIR, f"enriched_links_{timestamp}.json")
    latest_file = os.path.join(ENRICH_DIR, "enriched_links_latest.json")

    with open(archive_file, 'w', encoding='utf-8') as f:
        json.dump(high_value_links, f, ensure_ascii=False, indent=2)
    with open(latest_file, 'w', encoding='utf-8') as f:
        json.dump(high_value_links, f, ensure_ascii=False, indent=2)
        
    print(f"\n[SUCCESS] 认知拓扑网提纯完成！")
    print(f"[-] 高价值留存数 (CIF >= 1.0): {len(high_value_links)}")
    print(f"[IO] 提纯表已存档至: {ENRICH_DIR}/")

if __name__ == '__main__':
    if sys.platform == 'win32':
        os.system('chcp 65001')
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())