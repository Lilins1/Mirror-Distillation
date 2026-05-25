"""统一 LLM 调用客户端 — JSON/文本双模式，内置重试与余额检测"""

import json
import asyncio
import logging
from typing import Optional

from openai import AsyncOpenAI

from .config import DeepSeekConfig

logger = logging.getLogger(__name__)


class LLMClient:
    """统一的 DeepSeek API 调用封装，所有 LLM 调用共用此类"""

    def __init__(self, ds_config: DeepSeekConfig):
        self._client = AsyncOpenAI(api_key=ds_config.api_key, base_url=ds_config.base_url)
        self.quota_exhausted = False

    async def chat_json(self, model: str, system: str, user: str,
                        max_tokens: int, temperature: float = 0.3,
                        retry: int = 3) -> Optional[dict]:
        """JSON 模式调用，自动解析 JSON，内置重试 + 余额检测"""
        for attempt in range(retry):
            try:
                resp = await self._client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                )
                return json.loads(resp.choices[0].message.content)
            except json.JSONDecodeError as e:
                logger.warning("JSON解析失败 (attempt %d/%d): %s", attempt + 1, retry, e)
                if attempt < retry - 1:
                    await asyncio.sleep(2 * (attempt + 1))
            except Exception as e:
                abort, delay = self._classify_error(e, attempt, retry)
                if abort:
                    return None
                if delay and attempt < retry - 1:
                    await asyncio.sleep(delay)

        return None

    async def chat_text(self, model: str, system: str, user: str,
                        max_tokens: int, temperature: float = 0.3,
                        retry: int = 3) -> Optional[str]:
        """纯文本模式调用，内置重试 + 余额检测"""
        for attempt in range(retry):
            try:
                resp = await self._client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return resp.choices[0].message.content or ""
            except Exception as e:
                abort, delay = self._classify_error(e, attempt, retry)
                if abort:
                    return None
                if delay and attempt < retry - 1:
                    await asyncio.sleep(delay)

        return None

    def _classify_error(self, e: Exception, attempt: int, retry: int) -> tuple:
        """返回 (abort, sleep_seconds)。abort=True 时调用方应 return None"""
        err = str(e).lower()
        if "402" in err or "insufficient balance" in err or "quota" in err:
            logger.error("余额不足: %s", e)
            self.quota_exhausted = True
            return (True, 0)
        if "429" in err or "rate" in err:
            delay = 10 * (attempt + 1)
            logger.warning("速率限制，%ds 后重试", delay)
            return (False, delay)
        if "context" in err or "length" in err:
            logger.warning("上下文超长，放弃: %s", e)
            return (True, 0)
        delay = 3 * (attempt + 1)
        logger.warning("API异常 (attempt %d/%d): %s", attempt + 1, retry, e)
        return (False, delay)
