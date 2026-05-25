"""Bilibili Wbi 签名 — 所有需要签名的 API 共用此模块"""

import hashlib
import time
import logging
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs

import httpx

logger = logging.getLogger(__name__)

# Wbi 固定的混合索引表
_MIXIN_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52
]


def _get_mixin_key(orig: str) -> str:
    return "".join(orig[n] for n in _MIXIN_ENC_TAB if n < len(orig))[:32]


class WbiSigner:
    """Wbi 签名器 — 缓存 img_key/sub_key，为请求参数添加 w_rid + wts"""

    _WEB_LOCATION = "1550101"  # B站 web 端位置标识

    def __init__(self):
        self._img_key: str = ""
        self._sub_key: str = ""
        self._last_fetch_time: float = 0.0
        self._ttl: float = 240.0  # 4 分钟刷新一次 key（避免长休眠后过期）

    async def _ensure_keys(self, client: httpx.AsyncClient):
        if self._img_key and self._sub_key and (time.time() - self._last_fetch_time) < self._ttl:
            return
        try:
            resp = await client.get("https://api.bilibili.com/x/web-interface/nav")
            if resp.status_code != 200:
                logger.warning("Wbi 密钥获取失败 (status=%d)", resp.status_code)
                self._img_key = ""
                self._sub_key = ""
                return
            data = resp.json()
            wbi = data.get("data", {}).get("wbi_img", {})
            img_url = wbi.get("img_url", "")
            sub_url = wbi.get("sub_url", "")
            if img_url:
                self._img_key = img_url.rsplit("/", 1)[-1].split(".")[0]
            if sub_url:
                self._sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0]
            if not self._img_key or not self._sub_key:
                logger.warning("Wbi 密钥为空，签名将无效")
            self._last_fetch_time = time.time()
        except Exception as e:
            logger.warning("Wbi 密钥获取异常: %s", e)
            self._img_key = ""
            self._sub_key = ""

    def recalculate_keys(self):
        """强制下一次请求重新获取 Wbi 密钥"""
        self._img_key = ""
        self._sub_key = ""
        self._last_fetch_time = 0.0

    def sign(self, params: dict) -> dict:
        """原地添加 wts / w_rid，返回同一 dict"""
        if "web_location" not in params:
            params["web_location"] = self._WEB_LOCATION
        mixin = _get_mixin_key(self._img_key + self._sub_key)
        params["wts"] = int(time.time())
        query = urlencode(sorted(params.items()))
        params["w_rid"] = hashlib.md5((query + mixin).encode()).hexdigest()
        return params

    async def signed_get(self, client: httpx.AsyncClient, url: str,
                         params: dict = None, **kwargs) -> httpx.Response:
        """对 URL 的 query params 进行签名后发送 GET，返回完整响应对象"""
        await self._ensure_keys(client)
        parsed = urlparse(url)
        existing = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        all_params = {**existing, **(params or {})}
        self.sign(all_params)
        signed_url = urlunparse(parsed._replace(query=urlencode(all_params)))
        return await client.get(signed_url, **kwargs)

    async def signed_get_json(self, client: httpx.AsyncClient, url: str,
                              params: dict = None, **kwargs) -> dict:
        """签名 GET 并返回 JSON。非 200 时打印响应体前 300 字符"""
        resp = await self.signed_get(client, url, params, **kwargs)
        if resp.status_code == 200:
            return resp.json()
        logger.warning("Wbi API %d: %s ... %s",
                       resp.status_code, url[:80], resp.text[:300])
        # 412/403 可能是密钥过期，清理后下次自动刷新
        if resp.status_code in (403, 412):
            self.recalculate_keys()
        try:
            return resp.json()
        except Exception:
            return {}
