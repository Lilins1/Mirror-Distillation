"""UP 主人物 Skill 管线 — 抓取关注的高粉 UP，按专注领域分类，
提取视频内容，参照 nuwa-skill 模板生成第一人称人物 SKILL.md

复用: BilibiliAuth / DataStorage / Stage2Extractor.run_batch() / Stage3Summarizer.run_batch()
"""

import os
import json
import math
import random
import shutil
import time
import asyncio
import logging
from datetime import datetime
from collections import defaultdict, Counter
from typing import Optional

import httpx
from openai import AsyncOpenAI

from .config import PipelineConfig, DeepSeekConfig
from .auth import BilibiliAuth
from .storage import DataStorage
from .extractor import Stage2Extractor
from .summarizer import Stage3Summarizer
from .wbi import WbiSigner

logger = logging.getLogger(__name__)

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]


def _random_ua() -> str:
    return random.choice(_USER_AGENTS)

# ═══════════════════════════════════════════════════════════════════
# Bilibili 分区名 → 领域文件夹名 映射
# ═══════════════════════════════════════════════════════════════════

DOMAIN_CATEGORY_MAP = {
    # 科技与数字
    "科技": "科技", "数码": "科技", "计算机技术": "科技", "编程": "科技",
    "人工智能": "科技", "科学": "科技", "航模": "科技", "极客": "科技",
    # 知识人文
    "社科": "人文", "历史": "人文", "文学": "人文", "哲学": "人文",
    "人文": "人文", "法律": "人文", "心理": "人文", "设计": "人文",
    "教育": "人文", "语言": "人文", "演讲": "人文", "公开课": "人文",
    # 商业财经
    "财经": "商业", "商业": "商业", "金融": "商业", "创业": "商业",
    "管理": "商业", "职场": "商业", "营销": "商业",
    # 生活
    "生活": "生活", "美食": "生活", "健身": "生活", "时尚": "生活",
    "家居": "生活", "手工": "生活", "三农": "生活",
    # 影视娱乐
    "影视": "影视", "电影": "影视", "电视剧": "影视", "综艺": "影视",
    "纪录片": "影视", "动画": "影视",
    # 游戏
    "游戏": "游戏", "电竞": "游戏",
    # 音乐
    "音乐": "音乐", "演奏": "音乐", "翻唱": "音乐",
    # 汽车出行
    "汽车": "汽车", "摩托车": "汽车", "交通": "汽车",
    # 体育运动
    "体育": "体育", "足球": "体育", "篮球": "体育", "搏击": "体育",
    # 资讯
    "资讯": "资讯", "时事": "资讯", "军事": "资讯",
}


def _infer_domain(videos: dict) -> str:
    """根据视频的 tname 众数推断 UP 专注领域，未匹配返回 '其他'"""
    tnames = [v.get("metadata", {}).get("category", "") for v in videos.values()]
    tnames = [t for t in tnames if t]
    if not tnames:
        return "其他"
    # 取最常见的 tname
    top_tname = Counter(tnames).most_common(1)[0][0]
    return DOMAIN_CATEGORY_MAP.get(top_tname, "其他")


def _domain_prompt_context(domain: str, up: dict) -> str:
    """根据领域生成额外的提示词上下文"""
    contexts = {
        "科技": f"该 UP 主深耕科技/数码领域。分析时需关注其技术视角、对新技术趋势的判断逻辑、以及解决问题的工程思维。在表达 DNA 中注意是否使用了技术圈惯用语和类比方式。",
        "人文": f"该 UP 主深耕人文社科领域。分析时需关注其论证的严谨性、跨学科引用习惯、以及是否具备学术思维或大众科普风格。在表达 DNA 中注意叙事节奏与引经据典的密度。",
        "商业": f"该 UP 主深耕商业/财经领域。分析时需关注其商业判断框架（第一性原理 vs 案例类比）、风险偏好的表述方式、以及对市场/行业的分析视角。注意其是否遵循某种投资或管理流派。",
        "生活": f"该 UP 主深耕生活方式领域。分析时需关注其对消费、审美、生活哲学的倾向，以及推荐逻辑（体验驱动 vs 参数驱动）。",
        "影视": f"该 UP 主深耕影视/内容创作领域。分析时需关注其叙事分析框架、审美标准、以及创作理念。注意其评论风格和评价维度。",
        "游戏": f"该 UP 主深耕游戏领域。分析时需关注其游戏理解深度、策略思维、以及是否具备竞技分析或设计批评视角。",
        "音乐": f"该 UP 主深耕音乐领域。分析时需关注其音乐审美标准、技术分析与感性表达之间的平衡。",
        "汽车": f"该 UP 主深耕汽车/出行领域。分析时需关注其评测维度、参数 vs 体验的权重分配。",
        "体育": f"该 UP 主深耕体育领域。分析时需关注其对竞技策略的理解、数据分析 vs 直觉判断的倾向。",
        "资讯": f"该 UP 主深耕资讯/时事领域。分析时需关注其信息源选择、事件分析框架、以及是否表现出某种立场倾向。",
    }
    base = contexts.get(domain, f"该 UP 主专注领域为 {domain}。请结合该领域特点进行分析。")
    return f"**领域上下文**: {base}\n**领域标签**: {domain}"


# ═══════════════════════════════════════════════════════════════════

async def _fetch_json(client: httpx.AsyncClient, url: str, referer: str = "https://www.bilibili.com/") -> dict:
    h = {**client.headers, "Referer": referer}
    resp = await client.get(url, headers=h, timeout=15.0)
    return resp.json() if resp.status_code == 200 else {}


# ═══════════════════════════════════════════════════════════════════
# UpFollowFetcher
# ═══════════════════════════════════════════════════════════════════

class UpFollowFetcher:
    """获取登录用户关注的所有 UP，拉取基础信息并过滤"""

    def __init__(self, config: PipelineConfig):
        self._cfg = config
        self._auth = BilibiliAuth(config.account_dir, label="主账号")
        self._storage = DataStorage()
        self._wbi = WbiSigner()

    async def fetch(self) -> list[dict]:
        cred = await self._auth.get_credential()
        cookies = self._auth.get_cookies()
        headers = {"User-Agent": _random_ua(), "Referer": "https://www.bilibili.com/"}
        async with httpx.AsyncClient(cookies=cookies, headers=headers, timeout=15.0) as client:
            my_uid = await self._get_my_uid(client)
            logger.info("我的 UID: %s", my_uid)

            all_follows = await self._get_followings(client, my_uid)
            logger.info("共关注 %d 个 UP", len(all_follows))

            ups = []
            continuous = 0
            pause_threshold = random.randint(100, 150)  # 每 70-120 个 UP 休眠

            for i, mid in enumerate(all_follows):
                # 深度休眠防封控
                if continuous >= pause_threshold:
                    await self._deep_sleep()
                    continuous = 0
                    pause_threshold = random.randint(100, 150)

                info = await self._get_up_info(client, mid)
                continuous += 1
                if info:
                    ups.append(info)
                if (i + 1) % 20 == 0:
                    logger.info("  UP info: %d/%d (连续: %d, 休眠阈值: %d)",
                                i + 1, len(all_follows), continuous, pause_threshold)
                await asyncio.sleep(random.uniform(1.5, 3.5))

        threshold = self._cfg.up_follower_threshold
        qualified = [u for u in ups if u.get("follower_count", 0) >= threshold]
        qualified.sort(key=lambda u: u.get("follower_count", 0), reverse=True)

        logger.info("合格 UP (粉丝≥%d): %d 个", threshold, len(qualified))
        for u in qualified:
            logger.info("  %s | 粉丝: %d", u["name"], u["follower_count"])

        out = os.path.join(self._cfg.up_persona_dir, "qualified_ups.json")
        self._storage.safe_save_json(qualified, out)
        return qualified

    async def _get_my_uid(self, client: httpx.AsyncClient) -> int:
        data = await _fetch_json(client, "https://api.bilibili.com/x/web-interface/nav")
        return data.get("data", {}).get("mid", 0)

    async def _get_followings(self, client: httpx.AsyncClient, uid: int) -> list[int]:
        mids = []
        page = 1
        consecutive_failures = 0
        while True:
            url = f"https://api.bilibili.com/x/relation/followings?vmid={uid}&pn={page}&ps=50&order=desc"
            data = await _fetch_json(client, url)
            items = data.get("data", {}).get("list", [])

            if not items:
                consecutive_failures += 1
                if consecutive_failures >= 4:
                    logger.warning("  关注列表连续 %d 次空响应，停止翻页", consecutive_failures)
                    break
                backoff = min(10 * (2 ** consecutive_failures), 120)
                logger.info("  关注列表第 %d 页空响应，%d秒后重试...", page, backoff)
                await asyncio.sleep(backoff)
                continue
            consecutive_failures = 0

            for item in items:
                mids.append(item.get("mid", 0))
            if len(mids) >= data.get("data", {}).get("total", 0):
                break
            page += 1

            # 每 5 页深度休眠
            if (page - 1) % 5 == 0:
                deep = random.uniform(20, 40)
                logger.info("  关注列表翻页防封控，休眠 %.0f 秒...", deep)
                await asyncio.sleep(deep)
            else:
                await asyncio.sleep(random.uniform(3.0, 6.0))
        return mids

    async def _get_up_info(self, client: httpx.AsyncClient, mid: int) -> Optional[dict]:
        try:
            card = await _fetch_json(client, f"https://api.bilibili.com/x/web-interface/card?mid={mid}")
            cdata = card.get("data", {})
            card_info = cdata.get("card", {})
            if not card_info.get("name"):
                return None
            follower_count = cdata.get("follower", 0)
            if isinstance(follower_count, str):
                follower_count = self._parse_count_str(follower_count)
            # 使用 Wbi 签名的 space API
            space = await self._wbi.signed_get_json(
                client,
                "https://api.bilibili.com/x/space/wbi/acc/info",
                params={"mid": str(mid)}
            )
            sign = space.get("data", {}).get("sign", "")
            return {
                "mid": mid,
                "name": card_info.get("name", ""),
                "follower_count": follower_count,
                "face": card_info.get("face", ""),
                "sign": sign,
                "official": card_info.get("Official", {}).get("title", ""),
            }
        except Exception as e:
            logger.debug("获取 UP %d 信息失败: %s", mid, e)
            return None

    @staticmethod
    def _parse_count_str(s: str) -> int:
        s = str(s).strip()
        if "万" in s:
            return int(float(s.replace("万", "")) * 10000)
        try:
            return int(s)
        except ValueError:
            return 0

    @staticmethod
    async def _deep_sleep():
        """防封控深度休眠 5-12 分钟"""
        duration = random.uniform(300, 720)
        logger.warning("防封控深度休眠 %.1f 分钟...", duration / 60)
        remaining = duration
        while remaining > 0:
            chunk = min(60, remaining)
            await asyncio.sleep(chunk)
            remaining -= chunk
            if remaining > 0:
                logger.info("  休眠剩余 %.0f 秒...", remaining)


# ═══════════════════════════════════════════════════════════════════
# UpVideoCollector
# ═══════════════════════════════════════════════════════════════════

class UpVideoCollector:
    """获取指定 UP 主的播放量排序视频列表"""

    def __init__(self, config: PipelineConfig):
        self._cfg = config
        self._auth = BilibiliAuth(config.account_dir, label="主账号")
        self._storage = DataStorage()
        self._wbi = WbiSigner()

    async def collect(self, up: dict, max_videos: int = 0) -> dict:
        """返回 {bvid: enriched_node}。领域分类由后续 AI 步骤完成。"""
        mid = up["mid"]
        up_name = up["name"]
        fans = up.get("follower_count", 0)

        factor = self._cfg.up_view_threshold_factor
        max_cap = self._cfg.up_max_video_count
        raw_count = int((max(fans, 1) ** 0.6) * factor)
        max_count = max(10, min(raw_count, max_cap))
        if max_videos > 0:
            max_count = min(max_count, max_videos)

        logger.info("抓取 UP 视频: %s (mid=%d, 粉丝=%d, sqrt*%.2f=%d, 上限=%d, 最终=%d)",
                     up_name, mid, fans, factor, raw_count, max_cap, max_count)

        cookies = self._auth.get_cookies()
        headers = {"User-Agent": _random_ua(), "Referer": "https://space.bilibili.com/"}

        async with httpx.AsyncClient(cookies=cookies, headers=headers, timeout=15.0) as client:
            bvids = await self._fetch_up_bvids(client, mid, max_count)

        logger.info("  %s: 获取 %d 个视频", up_name, len(bvids))

        # 构造 enriched 格式
        videos = {}
        for bvid, info in bvids.items():
            duration_sec = self._parse_length(info.get("length", "0"))
            videos[bvid] = {
                "video_id": bvid,
                "metadata": {
                    "title": info.get("title", ""),
                    "duration": duration_sec,
                    "progress": duration_sec,
                    "view_at": info.get("created", 0),
                    "author": up_name,
                    "author_mid": mid,
                    "category": info.get("typename", "") or "",
                    "description": info.get("description", ""),
                    "tags": [],
                },
                "cognitive_impact_factor": 5.0,
                "interaction_status": {
                    "coin_count": 0, "is_favorited": False, "is_liked": False,
                },
            }

        return videos

    async def _fetch_up_bvids(self, client: httpx.AsyncClient, mid: int, count: int) -> dict:
        result = {}
        page = 1
        consecutive_failures = 0
        while len(result) < count:
            # 每 3 页做一次深度休眠
            if page > 1 and (page - 1) % 3 == 0:
                deep = random.uniform(30, 60)
                logger.info("  翻页防封控，休眠 %.0f 秒...", deep)
                await asyncio.sleep(deep)

            data = await self._wbi.signed_get_json(
                client,
                "https://api.bilibili.com/x/space/wbi/arc/search",
                params={"mid": str(mid), "ps": "50", "tid": "0", "pn": str(page), "order": "click"}
            )
            vlist = data.get("data", {}).get("list", {}).get("vlist", [])
            code = data.get("code", 0)

            # 被风控 (data为空dict) 或 API 返回异常 code
            is_blocked = not data
            is_error = code != 0
            if is_blocked or (not vlist and is_error):
                consecutive_failures += 1
                if consecutive_failures >= 4:
                    logger.warning("  连续 %d 次异常，停止翻页", consecutive_failures)
                    break
                backoff = min(15 * (2 ** consecutive_failures), 300)
                reason = "被风控(HTML)" if is_blocked else f"code={code}"
                logger.info("  API 异常 (%s)，%d秒后重试第 %d 页...", reason, backoff, page)
                await asyncio.sleep(backoff)
                continue

            if not vlist:
                break
            consecutive_failures = 0

            for v in vlist:
                bvid = v.get("bvid")
                if bvid:
                    result[bvid] = v
                if len(result) >= count:
                    break
            if len(vlist) < 50:
                break
            page += 1
            await asyncio.sleep(random.uniform(8.0, 15.0))
        return result

    @staticmethod
    def _parse_length(length: str) -> int:
        """将 B站 space API 的 length 字段 (MM:SS 或 HH:MM:SS) 转为总秒数"""
        if not length or length == "0":
            return 0
        try:
            parts = length.strip().split(":")
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            elif len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            return int(length)
        except (ValueError, AttributeError):
            return 0


# ═══════════════════════════════════════════════════════════════════
# UpResearchBuilder
# ═══════════════════════════════════════════════════════════════════

class UpResearchBuilder:
    """将 UP 视频的 LLM 总结聚合为 5 维研究 MD 报告"""

    def __init__(self, config: PipelineConfig):
        self._cfg = config
        self._storage = DataStorage()

    def build(self, up: dict, summaries: list[dict], domain: str = "其他") -> dict[str, str]:
        valid = [s for s in summaries if s.get("ai_distillation", {}).get("mode") != "failed"
                 and not s.get("ai_distillation", {}).get("is_ad_contaminated", False)]
        valid.sort(key=lambda x: x.get("cognitive_impact_factor", 0), reverse=True)

        up_name = up["name"]
        return {
            "01-content-overview.md": self._gen_overview(up, valid, domain),
            "02-thinking-patterns.md": self._gen_thinking(up, valid),
            "03-expression-style.md": self._gen_expression(up, valid),
            "04-values-boundaries.md": self._gen_values(up, valid),
            "05-timeline.md": self._gen_timeline(up, valid),
        }

    def _gen_overview(self, up: dict, nodes: list, domain: str) -> str:
        md = f"# {up['name']} · 内容概览\n\n"
        md += f"> 领域: {domain} | 粉丝: {up.get('follower_count', '?')} | 签名: {up.get('sign', '')}\n"
        md += f"> 分析视频数: {len(nodes)}\n\n"
        cats = defaultdict(list)
        for n in nodes:
            cat = n.get("ai_distillation", {}).get("tags", {}).get("primary_category", "未分类")
            cats[cat].append(n)
        for cat, items in cats.items():
            md += f"## {cat} ({len(items)}个)\n"
            for n in items[:8]:
                title = n.get("metadata", {}).get("title", "")
                summary = n.get("ai_distillation", {}).get("summary", "")
                ks = n.get("ai_distillation", {}).get("knowledge_value_score", 0)
                md += f"- **[ks={ks}] {title}**\n  {summary[:200]}\n\n"
        return md

    def _gen_thinking(self, up: dict, nodes: list) -> str:
        md = f"# {up['name']} · 思维模式\n\n> 推理方式与认知框架\n\n"
        count = 0
        for n in nodes:
            cp = n.get("ai_distillation", {}).get("cognitive_profile", {})
            think = cp.get("thinking_mode", "")
            fw = cp.get("knowledge_framework", "")
            if self._valid(think) or self._valid(fw):
                md += f"### {n.get('metadata', {}).get('title', '')[:40]}\n"
                if self._valid(think):
                    md += f"- 思维: {think}\n"
                if self._valid(fw):
                    md += f"- 框架: {fw}\n"
                md += "\n"
                count += 1
                if count >= 25:
                    break
        return md

    def _gen_expression(self, up: dict, nodes: list) -> str:
        md = f"# {up['name']} · 表达风格\n\n> 语言特征、情绪基调、叙事节奏\n\n"
        count = 0
        for n in nodes:
            cp = n.get("ai_distillation", {}).get("cognitive_profile", {})
            style = cp.get("language_style", "")
            tone = cp.get("emotional_tone", "")
            arg = cp.get("argumentation_pattern", "")
            if self._valid(style) or self._valid(tone) or self._valid(arg):
                parts = []
                if self._valid(style):
                    parts.append(f"语言: {self._compact(style)}")
                if self._valid(tone):
                    parts.append(f"情绪: {self._compact(tone)}")
                if self._valid(arg):
                    parts.append(f"论证: {self._compact(arg)}")
                md += f"- **{n.get('metadata', {}).get('title', '')[:30]}**: " + " | ".join(parts) + "\n"
                count += 1
                if count >= 30:
                    break
        return md

    def _gen_values(self, up: dict, nodes: list) -> str:
        md = f"# {up['name']} · 价值观与边界\n\n> 底层信念、价值偏好、排斥信号\n\n"
        count = 0
        for n in nodes:
            cp = n.get("ai_distillation", {}).get("cognitive_profile", {})
            beliefs = cp.get("core_beliefs", "")
            values = cp.get("values_preferences", "")
            if self._valid(beliefs) or self._valid(values):
                md += f"### {n.get('metadata', {}).get('title', '')[:40]}\n"
                if self._valid(beliefs):
                    md += f"- 信念: {self._compact(beliefs)}\n"
                if self._valid(values):
                    md += f"- 价值: {self._compact(values)}\n"
                md += "\n"
                count += 1
                if count >= 20:
                    break
        return md

    def _gen_timeline(self, up: dict, nodes: list) -> str:
        md = f"# {up['name']} · 时间线\n\n"
        valid = [n for n in nodes if n.get("metadata", {}).get("view_at")]
        valid.sort(key=lambda x: x["metadata"]["view_at"])
        for n in valid:
            ts = n.get("metadata", {}).get("view_at", 0)
            dt = datetime.fromtimestamp(ts).strftime("%Y-%m") if ts else "未知"
            title = n.get("metadata", {}).get("title", "")[:40]
            cat = n.get("ai_distillation", {}).get("tags", {}).get("primary_category", "")
            md += f"- [{dt}] [{cat}] {title}\n"
        return md

    @staticmethod
    def _valid(text) -> bool:
        if not text or not isinstance(text, str):
            return False
        low = text.lower()
        return "insufficient_data" not in low and "无法推断" not in low

    @staticmethod
    def _compact(text: str, max_len: int = 120) -> str:
        t = " ".join(text.split())
        return t if len(t) <= max_len else t[:max_len - 3] + "..."


# ═══════════════════════════════════════════════════════════════════
# UpPersonaGenerator
# ═══════════════════════════════════════════════════════════════════

class UpPersonaGenerator:
    """使用 LLM 将 UP 研究数据渲染为第一人称人物 SKILL.md，并对 UP 进行领域分类"""

    # 可供 classify 使用的标准领域列表
    DOMAIN_OPTIONS = ["科技", "人文", "商业", "生活", "影视", "游戏", "音乐", "汽车", "体育", "资讯", "其他"]

    def __init__(self, config: PipelineConfig):
        self._cfg = config
        self._storage = DataStorage()

    async def classify_domain(self, up: dict, summaries: list[dict]) -> str:
        """基于视频标题与 AI 总结，让 LLM 推断 UP 专注领域。返回领域标签。"""
        if not summaries:
            return "其他"

        ds_config = DeepSeekConfig.from_file(self._cfg.deepseek_config_path)

        # 拼接视频标题 + 摘要（截断控制 token）
        sample_lines = []
        for s in summaries[:30]:
            title = s.get("metadata", {}).get("title", "")
            summary = s.get("ai_distillation", {}).get("summary", "")
            cat = s.get("ai_distillation", {}).get("tags", {}).get("primary_category", "")
            sample_lines.append(f"- [{cat}] {title[:60]}: {summary[:120]}")
        sample_text = "\n".join(sample_lines)

        domain_list = "、".join(self.DOMAIN_OPTIONS)
        system = f"你是内容领域分类专家。基于视频主题和知识内容，将 UP 主归类到最匹配的领域。可选领域: {domain_list}"

        user = f"""请分析以下 UP 主的视频内容，确定其专注领域。

UP 名称: {up['name']}
粉丝数: {up.get('follower_count', 0)}
签名: {up.get('sign', '')}

视频样本 (标题 + AI 总结):
---
{sample_text}
---

可选领域: {domain_list}

请严格按 JSON 输出:
{{"domain": "最匹配的领域", "confidence": "high/medium/low", "reason": "≤60字依据"}}

规则:
1. domain 必须从可选领域中选择，不编造新领域
2. 若视频横跨 >2 个领域且无明确主导，选 "其他"
3. 以视频内容为准，不依赖 UP 签名或名称
4. confidence: high=≥80%把握, medium=60-80%, low=<60%"""

        client = AsyncOpenAI(api_key=ds_config.api_key, base_url=ds_config.base_url)
        try:
            resp = await client.chat.completions.create(
                model=self._cfg.model_small,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.1,
                max_tokens=300,
                response_format={"type": "json_object"},
            )
            result = json.loads(resp.choices[0].message.content or "{}")
            domain = result.get("domain", "其他")
            confidence = result.get("confidence", "low")
            reason = result.get("reason", "")
            # 验证 domain 合法性
            if domain not in self.DOMAIN_OPTIONS:
                domain = "其他"
            logger.info("  AI 领域分类: %s (confidence=%s) — %s", domain, confidence, reason)
            return domain
        except Exception as e:
            logger.warning("  领域分类失败，降级为 '其他': %s", e)
            return "其他"

    async def generate(self, up: dict, up_dir: str, domain: str = "其他") -> Optional[str]:
        """生成人物 SKILL.md。domain 用于注入领域上下文提示。"""
        ds_config = DeepSeekConfig.from_file(self._cfg.deepseek_config_path)

        research_dir = os.path.join(up_dir, "research")
        all_research = ""
        for fn in sorted(os.listdir(research_dir)):
            if fn.endswith(".md"):
                text = self._storage.read_text(os.path.join(research_dir, fn))
                if text:
                    all_research += f"\n\n--- {fn} ---\n\n{text}"

        if not all_research.strip():
            logger.warning("UP %s 无有效研究数据", up["name"])
            return None

        template = self._storage.read_text(self._cfg.up_skill_template_path)
        if not template:
            template = self._storage.read_text("references/nuwa-skill/skill-template.md")
        framework = self._storage.read_text(self._cfg.up_framework_path)
        if not framework:
            framework = self._storage.read_text("references/nuwa-skill/extraction-framework.md")

        if not template or not framework:
            logger.error("缺少 nuwa-skill 模板或方法论文档")
            return None

        domain_ctx = _domain_prompt_context(domain, up)
        skill = await self._call_llm(ds_config, up, framework, all_research, template, domain_ctx)
        return skill

    async def _call_llm(self, ds: DeepSeekConfig, up: dict,
                         framework: str, research: str, template: str,
                         domain_ctx: str) -> Optional[str]:
        system = (
            "你是 Nuwa 人物建模引擎。基于研究语料生成该人物的 SKILL.md。"
            "严格遵守：只依据研究语料；禁止空泛套话；不足填'insufficient_data'；"
            "以第一人称'我'撰写角色扮演规则；证据不足标注为推测。"
        )

        user = f"""你正在为 **{up['name']}** 生成人物 Skill。

{domain_ctx}

方法论文档（硬约束）：
---
{framework}
---

研究语料：
---
{research}
---

模板（必须遵守此结构）：
---
{template}
---

硬性要求：
1. 直接输出最终 Markdown（无 JSON、无解释）。
2. 角色扮演规则：以第一人称「我」回应，用 {up['name']} 的语气和视角。
3. 心智模型需有跨域复现证据（≥2 领域），不足 3 个不编造。结合上述领域上下文判断其专业思维特征。
4. 决策启发式需有具体案例支撑，必须是默认动作规则。
5. 表达 DNA 需有辨识度：句式、词汇、节奏、幽默、确定性、引用习惯。
6. 必须包含诚实边界，标注信息源和调研时间。
7. 内在张力（矛盾）是加分项，不要抹平。
8. 调研来源引用具体的 BV 号。
9. 输出纯 Markdown，不要代码块包裹。
10. 身份卡中应体现其专注领域特征。

生成策略：先在脑中证据筛选，按模板结构填充。不确定的写推测或不足。"""

        client = AsyncOpenAI(api_key=ds.api_key, base_url=ds.base_url)
        try:
            resp = await client.chat.completions.create(
                model=self._cfg.up_model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.3,
                max_tokens=24000,
            )
            raw = resp.choices[0].message.content or ""
            return self._strip_fences(raw)
        except Exception as e:
            logger.error("生成 %s 的 Skill 失败: %s", up["name"], e)
            return None

    @staticmethod
    def _strip_fences(text: str) -> str:
        t = text.strip()
        for prefix in ("```json", "```markdown", "```"):
            if t.startswith(prefix):
                t = t[len(prefix):].strip()
        if t.endswith("```"):
            t = t[:-3].strip()
        return t


# ═══════════════════════════════════════════════════════════════════
# UpPersonaPipeline — 总调度器
# ═══════════════════════════════════════════════════════════════════

class UpPersonaPipeline:
    """UP 主人物 Skill 生成管线总调度

    流程:
    1. 拉取关注列表，筛选高粉 UP
    2. 对每个 UP: 抓取视频 → 提取字幕 → LLM 总结 → AI 领域分类 → 研究聚合 → 生成 SKILL.md
    3. 输出结构: data/up_persona/{领域}/{username}/SKILL.md
    """

    def __init__(self, config: PipelineConfig):
        self._cfg = config
        self._storage = DataStorage()

    async def run(self, single_uid: int = None, limit: int = 0, max_videos: int = 0) -> None:
        if not self._cfg.enable_up_persona:
            logger.info("UP Persona 管线未启用，跳过")
            return

        logger.info("=" * 60)
        logger.info("UP Persona Pipeline: 人物 Skill 生成 (增量模式)")
        logger.info("=" * 60)

        # Step 1: 获取合格 UP 列表
        if single_uid:
            logger.info("单 UP 测试模式: mid=%d", single_uid)
            fetcher = UpFollowFetcher(self._cfg)
            cookies = fetcher._auth.get_cookies()
            headers = {"User-Agent": _random_ua()}
            async with httpx.AsyncClient(cookies=cookies, headers=headers, timeout=15.0) as client:
                info = await fetcher._get_up_info(client, single_uid)
            if not info:
                logger.error("无法获取 UP %d 信息", single_uid)
                return
            qualified = [info]
        else:
            fetcher = UpFollowFetcher(self._cfg)
            qualified_ups_file = os.path.join(self._cfg.up_persona_dir, "qualified_ups.json")

            if os.path.exists(qualified_ups_file):
                qualified = self._storage.load_json(qualified_ups_file)
                logger.info("从缓存加载 %d 个合格 UP", len(qualified))
            else:
                qualified = await fetcher.fetch()
                if not qualified:
                    logger.warning("无符合粉丝门槛的 UP")
                    return

        if limit > 0:
            qualified = qualified[:limit]
            logger.info("限制处理 %d 个 UP", limit)

        # Step 2: 逐个 UP 处理
        collector = UpVideoCollector(self._cfg)
        extractor = Stage2Extractor(self._cfg)
        summarizer = Stage3Summarizer(self._cfg)
        research_builder = UpResearchBuilder(self._cfg)
        persona_gen = UpPersonaGenerator(self._cfg)

        domain_counts = Counter()

        for i, up in enumerate(qualified):
            up_name = up["name"]
            mid = up["mid"]
            dir_name = self._sanitize_name(up_name)
            logger.info("\n[UP %d/%d] %s (粉丝: %d)", i + 1, len(qualified), up_name, up["follower_count"])

            try:
                # ─── 2a: 查找已有数据（增量起点） ───
                existing_dir = self._find_existing_dir(dir_name)
                old_videos = {}
                old_summaries = []
                if existing_dir:
                    old_idx_path = os.path.join(existing_dir, "videos_index.json")
                    if os.path.exists(old_idx_path):
                        old_videos_all = self._storage.load_json_or_default(old_idx_path, {})
                        # 去掉 _domain 等元信息
                        old_videos = {k: v for k, v in old_videos_all.items() if k.startswith("BV")}
                    old_sum_dir = os.path.join(existing_dir, "summaries")
                    if os.path.exists(old_sum_dir):
                        for fn in os.listdir(old_sum_dir):
                            if fn.endswith(".json"):
                                try:
                                    old_summaries.append(self._storage.load_json(os.path.join(old_sum_dir, fn)))
                                except Exception:
                                    pass
                    logger.info("  已有 %d 个旧视频, %d 个旧总结 → %s",
                                len(old_videos), len(old_summaries), existing_dir)

                # ─── 2b: 抓取新视频（全量API拉取，与旧索引比对） ───
                fresh_videos = await collector.collect(up, max_videos=max_videos)
                if not fresh_videos:
                    if not old_videos:
                        logger.warning("  %s 无任何视频，跳过", up_name)
                        continue
                    fresh_videos = {}

                # 合并: 新视频覆盖旧视频元数据
                all_videos = {**old_videos, **fresh_videos}
                old_bvids = set(old_videos.keys())
                new_bvids = [b for b in fresh_videos if b not in old_bvids]
                logger.info("  合并后 %d 个视频 (新增 %d)", len(all_videos), len(new_bvids))

                # ─── 2c: 准备增量工作目录 ───
                work_dir = os.path.join(self._cfg.up_persona_dir, dir_name)
                sub_dir = os.path.join(work_dir, "subtitles")
                sum_dir = os.path.join(work_dir, "summaries")
                sub_progress = os.path.join(work_dir, "subtitle_progress.json")
                sum_progress = os.path.join(work_dir, "summary_progress.json")
                os.makedirs(work_dir, exist_ok=True)

                # 如果已有旧数据，把旧进度拷贝过来（让 run_batch 跳过已处理的）
                if existing_dir and not os.path.exists(sub_progress):
                    old_sub_prog = os.path.join(existing_dir, "subtitle_progress.json")
                    if os.path.exists(old_sub_prog):
                        shutil.copy2(old_sub_prog, sub_progress)
                if existing_dir and not os.path.exists(sum_progress):
                    old_sum_prog = os.path.join(existing_dir, "summary_progress.json")
                    if os.path.exists(old_sum_prog):
                        shutil.copy2(old_sum_prog, sum_progress)

                self._storage.safe_save_json(all_videos, os.path.join(work_dir, "videos_index.json"))

                # ─── 2d: 只处理新视频的字幕 + 总结 ───
                if new_bvids:
                    new_videos_dict = {b: all_videos[b] for b in new_bvids}
                    logger.info("  [1/5] 提取字幕 (%d 个新视频)...", len(new_bvids))
                    await extractor.run_batch(new_videos_dict, sub_dir, sub_progress)

                    logger.info("  [2/5] LLM 总结 (%d 个新视频)...", len(new_bvids))
                    await summarizer.run_batch(new_videos_dict, sub_dir, sum_dir, sum_progress)
                else:
                    # 没新视频，确保旧版字幕/总结可用
                    logger.info("  [1/5] 无新视频，跳过字幕提取")
                    logger.info("  [2/5] 无新视频，跳过 LLM 总结")
                    if existing_dir:
                        self._merge_dirs(os.path.join(existing_dir, "subtitles"), sub_dir)
                        self._merge_dirs(os.path.join(existing_dir, "summaries"), sum_dir)

                # ─── 2e: 加载全部总结（旧 + 新） ───
                all_summaries: list[dict] = list(old_summaries)
                seen_bvids = {s.get("video_id", "") for s in all_summaries}
                for bvid in all_videos:
                    sp = os.path.join(sum_dir, f"{bvid}.json")
                    if bvid not in seen_bvids and os.path.exists(sp):
                        try:
                            all_summaries.append(self._storage.load_json(sp))
                        except Exception:
                            pass
                logger.info("  总计 %d 个有效总结", len(all_summaries))

                if not all_summaries:
                    logger.warning("  %s 无有效总结，跳过", up_name)
                    continue

                # ─── 2f: AI 领域分类 ───
                logger.info("  [3/5] AI 领域分类...")
                domain = await persona_gen.classify_domain(up, all_summaries)
                old_domain = self._domain_from_path(existing_dir) if existing_dir else None
                if old_domain and old_domain != domain:
                    logger.info("  领域变更: %s → %s", old_domain, domain)

                # ─── 2g: 迁移到最终领域目录 ───
                domain_dir = os.path.join(self._cfg.up_persona_dir, domain)
                final_dir = os.path.join(domain_dir, dir_name)
                os.makedirs(domain_dir, exist_ok=True)

                # 合并移动（不删旧目录，用 shutil.move 增量合并）
                if os.path.exists(final_dir) and final_dir != work_dir:
                    # 先把 work_dir 内容合并到 final_dir
                    self._merge_dirs(work_dir, final_dir)
                    if work_dir != final_dir and os.path.exists(work_dir):
                        shutil.rmtree(work_dir)
                elif work_dir != final_dir:
                    if os.path.exists(final_dir):
                        shutil.rmtree(final_dir)
                    os.rename(work_dir, final_dir)

                # 更新路径到最终目录
                research_dir = os.path.join(final_dir, "research")
                skill_path = os.path.join(final_dir, "SKILL.md")

                # ─── 2h: 研究聚合 ───
                logger.info("  [4/5] 研究聚合 (%s)...", domain)
                reports = research_builder.build(up, all_summaries, domain)
                os.makedirs(research_dir, exist_ok=True)
                for fn, content in reports.items():
                    self._storage.write_text(os.path.join(research_dir, fn), content)
                logger.info("  → %d 个报告", len(reports))

                # ─── 2i: 生成 SKILL.md ───
                logger.info("  [5/5] 生成 SKILL.md (%s)...", domain)
                skill_md = await persona_gen.generate(up, final_dir, domain)
                if skill_md:
                    self._storage.write_text(skill_path, skill_md)
                    logger.info("  ✅ → %s", skill_path)
                    domain_counts[domain] += 1
                else:
                    logger.error("  ❌ 生成失败: %s", up_name)

            except Exception as e:
                logger.exception("处理 UP %s 异常: %s", up_name, e)
                continue

        # 汇总
        logger.info("\n" + "=" * 60)
        logger.info("UP Persona Pipeline 完成!")
        logger.info("输出根目录: %s/", self._cfg.up_persona_dir)
        logger.info("领域分布:")
        for domain, count in domain_counts.most_common():
            logger.info("  %s/: %d 个", domain, count)
        logger.info("=" * 60)

    # ─── 增量相关辅助方法 ───

    def _find_existing_dir(self, dir_name: str) -> Optional[str]:
        """扫描所有领域子目录，找到该用户名的已有数据目录"""
        base = self._cfg.up_persona_dir
        if not os.path.exists(base):
            return None
        for entry in os.listdir(base):
            candidate = os.path.join(base, entry, dir_name)
            if os.path.isdir(candidate):
                skill_path = os.path.join(candidate, "SKILL.md")
                if os.path.exists(skill_path):
                    return candidate
        # 回退: 找有 summaries 的目录
        for entry in os.listdir(base):
            candidate = os.path.join(base, entry, dir_name)
            if os.path.isdir(candidate):
                sum_dir = os.path.join(candidate, "summaries")
                if os.path.exists(sum_dir) and os.listdir(sum_dir):
                    return candidate
        return None

    @staticmethod
    def _sanitize_name(name: str) -> str:
        """将 UP 用户名转为安全的目录名（过滤 Windows 非法字符）"""
        for c in '<>:"/\\|?*':
            name = name.replace(c, '_')
        name = name.strip('. ')
        reserved = {'CON', 'PRN', 'AUX', 'NUL',
                    *(f'COM{n}' for n in range(1, 10)),
                    *(f'LPT{n}' for n in range(1, 10))}
        if not name or name.upper() in reserved:
            name = '_' + name
        return name

    @staticmethod
    def _domain_from_path(dirpath: str) -> Optional[str]:
        """从路径 data/up_persona/{domain}/{username} 提取 domain"""
        if not dirpath:
            return None
        parts = dirpath.replace("\\", "/").rstrip("/").split("/")
        # 倒数第二个元素是 domain
        if len(parts) >= 2:
            return parts[-2]
        return None

    @staticmethod
    def _merge_dirs(src: str, dst: str):
        """将 src 目录内容合并到 dst（覆盖同名文件）"""
        if not os.path.exists(src):
            return
        os.makedirs(dst, exist_ok=True)
        for name in os.listdir(src):
            s = os.path.join(src, name)
            d = os.path.join(dst, name)
            if os.path.isdir(s):
                UpPersonaPipeline._merge_dirs(s, d)
            else:
                shutil.copy2(s, d)

    # ==================== CLI ====================

    @classmethod
    def main(cls):
        import sys
        import argparse
        logging.basicConfig(level=logging.INFO, format="[%(levelname)-5s] %(message)s")
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("openai").setLevel(logging.WARNING)

        if sys.platform == "win32":
            import os as _os
            _os.system("chcp 65001")
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        parser = argparse.ArgumentParser(description="UP 主人物 Skill 管线")
        parser.add_argument("--debug", action="store_true")
        parser.add_argument("--threshold", type=int, default=None, help="粉丝数阈值")
        parser.add_argument("--uid", type=int, default=None, help="指定 UP mid，跳过关注列表直接测试全流程")
        parser.add_argument("--limit", type=int, default=0, help="仅处理前 N 个 UP")
        parser.add_argument("--max-videos", type=int, default=0, help="每个 UP 最多抓取视频数 (0=自动计算)")
        args = parser.parse_args()

        cfg = PipelineConfig()
        cfg.enable_up_persona = True
        if args.debug:
            cfg.debug_mode = True
        if args.threshold is not None:
            cfg.up_follower_threshold = args.threshold

        try:
            asyncio.run(cls(cfg).run(single_uid=args.uid, limit=args.limit, max_videos=args.max_videos))
        except KeyboardInterrupt:
            logger.warning("收到中断信号")


if __name__ == "__main__":
    UpPersonaPipeline.main()
