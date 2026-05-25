"""UP 相关 API 测试脚本 — 逐步验证每个接口的返回内容

用法:
  python -m scripts.test_up_api              # 完整测试
  python -m scripts.test_up_api --uid <mid>  # 测试指定 UP
"""

import os
import sys
import json
import asyncio
import argparse
import logging

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.auth import BilibiliAuth
from scripts.config import PipelineConfig
from scripts.wbi import WbiSigner

logging.basicConfig(level=logging.INFO, format="[%(levelname)-5s] %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("test")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"


async def test_auth(config: PipelineConfig):
    """测试 1: 凭证加载"""
    print("\n" + "=" * 60)
    print("测试 1: 凭证加载")
    print("=" * 60)
    auth = BilibiliAuth(config.account_dir, label="主账号")
    cred = await auth.get_credential()
    print(f"  SESSDATA: {cred.sessdata[:20]}...")
    print(f"  bili_jct: {cred.bili_jct[:10]}...")
    return auth


async def test_my_uid(auth: BilibiliAuth, wbi: WbiSigner):
    """测试 2: 获取自己的 UID + Wbi keys"""
    print("\n" + "=" * 60)
    print("测试 2: /x/web-interface/nav — 获取 UID 与 Wbi 密钥")
    print("=" * 60)
    cookies = auth.get_cookies()
    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(cookies=cookies, headers=headers, timeout=15.0) as client:
        resp = await client.get("https://api.bilibili.com/x/web-interface/nav")
        data = resp.json()
        mid = data.get("data", {}).get("mid", 0)
        uname = data.get("data", {}).get("uname", "")
        print(f"  UID: {mid}")
        print(f"  用户名: {uname}")
        # Wbi keys
        wbi_img = data.get("data", {}).get("wbi_img", {})
        img_url = wbi_img.get("img_url", "")
        sub_url = wbi_img.get("sub_url", "")
        print(f"  img_key: {img_url.rsplit('/')[-1].split('.')[0] if img_url else 'N/A'}")
        print(f"  sub_key: {sub_url.rsplit('/')[-1].split('.')[0] if sub_url else 'N/A'}")
        await wbi._ensure_keys(client)  # 预载入 keys
        return mid


async def test_followings(auth: BilibiliAuth, uid: int, max_pages: int = 2):
    """测试 3: 获取关注列表（前 N 页）"""
    print("\n" + "=" * 60)
    print(f"测试 3: /x/relation/followings — 关注列表 (前{max_pages}页)")
    print("=" * 60)
    cookies = auth.get_cookies()
    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(cookies=cookies, headers=headers, timeout=15.0) as client:
        all_mids = []
        for page in range(1, max_pages + 1):
            url = f"https://api.bilibili.com/x/relation/followings?vmid={uid}&pn={page}&ps=50&order=desc"
            resp = await client.get(url)
            data = resp.json() if resp.status_code == 200 else {}
            items = data.get("data", {}).get("list", [])
            total = data.get("data", {}).get("total", 0)
            if not items:
                break
            for item in items:
                all_mids.append({
                    "mid": item.get("mid"),
                    "uname": item.get("uname", ""),
                })
            print(f"  第{page}页: {len(items)}条 (总关注: {total})")
        print(f"\n  共获取 {len(all_mids)} 个关注")
        if all_mids:
            print("  前5个:")
            for u in all_mids[:5]:
                print(f"    mid={u['mid']} | {u['uname']}")
        return all_mids


async def test_up_card(auth: BilibiliAuth, mid: int):
    """测试 4: 获取 UP 卡片信息"""
    print("\n" + "=" * 60)
    print(f"测试 4: /x/web-interface/card — UP 卡片 (mid={mid})")
    print("=" * 60)
    cookies = auth.get_cookies()
    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(cookies=cookies, headers=headers, timeout=15.0) as client:
        resp = await client.get(f"https://api.bilibili.com/x/web-interface/card?mid={mid}")
        data = resp.json() if resp.status_code == 200 else {}
        cdata = data.get("data", {})
        card = cdata.get("card", {})
        print(f"  name: {card.get('name')}")
        print(f"  mid: {card.get('mid')}")
        print(f"  follower: {cdata.get('follower')}")
        print(f"  archive_count: {cdata.get('archive_count')}")
        print(f"  official: {card.get('Official', {}).get('title', '')}")
        return card


async def test_up_space(auth: BilibiliAuth, wbi: WbiSigner, mid: int):
    """测试 5: 获取 UP 空间信息 (Wbi 签名)"""
    print("\n" + "=" * 60)
    print(f"测试 5: /x/space/wbi/acc/info — UP 空间信息 (mid={mid}) [Wbi 签名]")
    print("=" * 60)
    cookies = auth.get_cookies()
    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(cookies=cookies, headers=headers, timeout=15.0) as client:
        # 先试 wbi 签名的接口
        data = await wbi.signed_get_json(
            client,
            "https://api.bilibili.com/x/space/wbi/acc/info",
            params={"mid": str(mid)}
        )
        space_data = data.get("data", {})
        if not space_data:
            # 降级到旧接口
            print("  (降级到旧接口 /x/space/acc/info)")
            resp = await client.get(f"https://api.bilibili.com/x/space/acc/info?mid={mid}")
            space_data = (resp.json() if resp.status_code == 200 else {}).get("data", {})
        print(f"  name: {space_data.get('name')}")
        print(f"  sign: {space_data.get('sign', '')[:100]}")
        print(f"  sex: {space_data.get('sex')}")
        print(f"  level: {space_data.get('level')}")
        return space_data


async def test_up_videos(auth: BilibiliAuth, wbi: WbiSigner, mid: int, max_videos: int = 15):
    """测试 6: 获取 UP 视频列表 (Wbi 签名, 播放量降序)"""
    print("\n" + "=" * 60)
    print(f"测试 6: /x/space/wbi/arc/search — UP 视频 (mid={mid}, top{max_videos}) [Wbi 签名]")
    print("=" * 60)
    cookies = auth.get_cookies()
    headers = {"User-Agent": USER_AGENT, "Referer": "https://space.bilibili.com/"}
    async with httpx.AsyncClient(cookies=cookies, headers=headers, timeout=15.0) as client:
        result = {}
        page = 1
        while len(result) < max_videos:
            data = await wbi.signed_get_json(
                client,
                "https://api.bilibili.com/x/space/wbi/arc/search",
                params={"mid": str(mid), "ps": "50", "tid": "0", "pn": str(page), "order": "click"}
            )
            # 检查返回结构
            code = data.get("code")
            msg = data.get("message", "")
            if code != 0:
                print(f"  API 返回错误: code={code}, message={msg}")
                print(f"  完整响应: {json.dumps(data, ensure_ascii=False)[:400]}")
                break
            ddata = data.get("data", {})
            # B站返回结构为 data.list.vlist 或 data.list.tlist
            lst = ddata.get("list", {})
            vlist = lst.get("vlist", [])
            if not vlist:
                print(f"  第{page}页: 无数据")
                print(f"  data keys: {list(ddata.keys())}, list keys: {list(lst.keys())}")
                break
            for v in vlist:
                bvid = v.get("bvid", "")
                result[bvid] = {
                    "title": v.get("title", ""),
                    "play": v.get("play", 0),
                    "tname": v.get("typename", v.get("tname", "")),
                    "duration": v.get("length", ""),
                    "created": v.get("created", 0),
                }
                if len(result) >= max_videos:
                    break
            print(f"  第{page}页: {len(vlist)}个视频 (已收集 {len(result)})")
            if len(vlist) < 50:
                break
            page += 1
            await asyncio.sleep(0.3)
        print(f"\n  共获取 {len(result)} 个视频 (按播放量降序)")
        for i, (bvid, info) in enumerate(list(result.items())[:10], 1):
            print(f"  {i}. [{info['tname']}] {info['title'][:50]}")
            print(f"     BV: {bvid} | 播放: {info['play']} | 时长: {info['duration']}")
        return result




async def main():
    parser = argparse.ArgumentParser(description="测试 Bilibili UP 相关 API")
    parser.add_argument("--uid", type=int, default=None, help="指定测试的 UP mid")
    parser.add_argument("--pages", type=int, default=2, help="关注列表测试页数")
    parser.add_argument("--videos", type=int, default=15, help="视频抓取数量")
    args = parser.parse_args()

    if sys.platform == "win32":
        os.system("chcp 65001")
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    config = PipelineConfig()
    wbi = WbiSigner()

    auth = await test_auth(config)

    if args.uid:
        test_mid = args.uid
        print(f"\n>>> 直接测试 UP: mid={test_mid}")
    else:
        uid = await test_my_uid(auth, wbi)
        if not uid:
            print("获取 UID 失败，请检查凭证是否有效")
            return
        follows = await test_followings(auth, uid, max_pages=args.pages)
        if not follows:
            print("关注列表为空")
            return
        test_mid = follows[0]["mid"]
        print(f"\n>>> 选取第一个关注 UP 进行后续测试: mid={test_mid}")

    await test_up_card(auth, test_mid)
    await test_up_space(auth, wbi, test_mid)
    videos = await test_up_videos(auth, wbi, test_mid, max_videos=args.videos)

    if not videos:
        print("\n未获取到视频，请检查 API 返回的 code/message 定位原因")

    print("\n" + "=" * 60)
    print("所有 API 测试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
