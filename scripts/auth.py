"""Bilibili 认证模块 — 统一主号/小号 QR 登录与凭证管理"""

import os
import sys
import json
import random
import asyncio
import logging
import platform
import subprocess
from typing import Optional

import httpx
import qrcode
from bilibili_api import Credential

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]


def _get_random_ua() -> str:
    return random.choice(USER_AGENTS)


def _show_msgbox(account_label: str) -> None:
    """跨平台弹窗（独立线程执行，不阻塞主循环）"""
    msg = (
        f"【Mirror 蒸馏后台管线】\n\n"
        f"{account_label}凭证已过期/失效。\n"
        f"请使用 Bilibili App 扫描刚刚弹出的二维码图片！\n\n"
        f"（扫码确认后后台会自动恢复运行，您可以直接关闭此提示框和图片）"
    )
    title = f"⚠️ 需要扫码授权 ({account_label})"

    if platform.system() == "Windows":
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, title, 0x1000 | 0x40)
    else:
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            messagebox.showinfo(title, msg, parent=root)
            root.destroy()
        except Exception:
            pass


class BilibiliAuth:
    """Bilibili 扫码登录与凭证管理，支持主号和小号双轨"""

    def __init__(self, account_dir: str, label: str = "主账号"):
        self._account_dir = account_dir
        self._label = label
        os.makedirs(account_dir, exist_ok=True)
        self._credential_path = os.path.join(account_dir, "credential.json" if label == "主账号" else "guest_credential.json")
        self._qr_img_path = os.path.join(account_dir, "master_qr_temp.png" if label == "主账号" else "guest_qr_temp.png")

    # ---------- public API ----------

    async def get_credential(self) -> Credential:
        """获取有效凭证：优先从缓存加载并验证，失败则触发扫码登录"""
        cached = await self._try_load_cached()
        if cached is not None:
            return cached
        return await self._qr_login()

    def get_cookies(self) -> dict:
        """从本地凭证文件读取 cookies 字典（用于 httpx）"""
        if not os.path.exists(self._credential_path):
            raise FileNotFoundError(f"Credential not found: {self._credential_path}")
        with open(self._credential_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "SESSDATA": data.get("sessdata", ""),
            "bili_jct": data.get("bili_jct", ""),
            "buvid3": data.get("buvid3", ""),
        }

    # ---------- private ----------

    async def _try_load_cached(self) -> Optional[Credential]:
        if not os.path.exists(self._credential_path):
            return None
        try:
            with open(self._credential_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cred = Credential(
                sessdata=data.get("sessdata", ""),
                bili_jct=data.get("bili_jct", ""),
                buvid3=data.get("buvid3", ""),
            )
            if await cred.check_valid():
                return cred
        except Exception:
            pass
        return None

    async def _qr_login(self) -> Credential:
        logger.info("[%s] 发起 QR 扫码登录", self._label)
        headers = {"User-Agent": _get_random_ua()}

        async with httpx.AsyncClient(headers=headers, timeout=15.0) as client:
            init_resp = await client.get("https://www.bilibili.com")
            buvid3 = init_resp.cookies.get("buvid3", "")

            resp = await client.get(
                "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
            )
            data = resp.json()["data"]
            qr_url = data["url"]
            qrcode_key = data["qrcode_key"]

            # print QR to terminal
            qr = qrcode.QRCode()
            qr.add_data(qr_url)
            qr.print_ascii(invert=True)

            # save & open QR image
            self._show_qr_image(qr)
            asyncio.get_event_loop().run_in_executor(
                None, _show_msgbox, self._label
            )

            # poll for confirmation
            while True:
                await asyncio.sleep(2)
                poll_resp = await client.get(
                    f"https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key={qrcode_key}"
                )
                poll_data = poll_resp.json()["data"]
                code = poll_data["code"]

                if code == 0:
                    logger.info("[%s] 扫码确认成功", self._label)
                    self._cleanup_qr_image()
                    cookies = poll_resp.cookies
                    cred = Credential(
                        sessdata=cookies.get("SESSDATA"),
                        bili_jct=cookies.get("bili_jct"),
                        buvid3=buvid3,
                    )
                    self._save_credential(cred)
                    return cred
                elif code == 86038:
                    logger.error("[%s] 二维码已过期", self._label)
                    sys.exit(1)
                elif code == 86090:
                    logger.info("[%s] 已扫码，请在手机端确认...", self._label)

    def _show_qr_image(self, qr: qrcode.QRCode) -> None:
        try:
            img = qr.make_image(fill_color="black", back_color="white")
            img.save(self._qr_img_path)
            system = platform.system()
            if system == "Windows":
                os.startfile(self._qr_img_path)
            elif system == "Darwin":
                subprocess.run(["open", self._qr_img_path])
            else:
                subprocess.run(["xdg-open", self._qr_img_path])
        except Exception as e:
            logger.warning("弹窗展示失败: %s", e)

    def _cleanup_qr_image(self) -> None:
        if os.path.exists(self._qr_img_path):
            try:
                os.remove(self._qr_img_path)
            except Exception:
                pass

    def _save_credential(self, cred: Credential) -> None:
        with open(self._credential_path, "w", encoding="utf-8") as f:
            json.dump({
                "sessdata": cred.sessdata,
                "bili_jct": cred.bili_jct,
                "buvid3": cred.buvid3,
            }, f, indent=4)
