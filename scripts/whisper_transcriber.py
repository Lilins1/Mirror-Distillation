"""策略3: 本地 Whisper 语音转文字 — bilibili-cli 下载音频 + faster-whisper 转录

B站 字幕和 AI 总结都拿不到时的最终兜底方案。
大会员视频通过 bilibili-cli 浏览器 Cookie 认证下载完整音频。
"""

import os
import time
import asyncio
import subprocess
import logging
from typing import Optional

from .config import PipelineConfig

logger = logging.getLogger(__name__)


class WhisperTranscriber:
    """下载 B站 视频音频流，使用本地 GPU/CPU 进行语音转文字"""

    def __init__(self, config: PipelineConfig):
        self._cfg = config
        self._model = None                                        # 懒加载，所有视频复用
        self._output_dir = os.path.join(config.data_dir, "tmp_audio")
        os.makedirs(self._output_dir, exist_ok=True)

    # ==================== public API ====================

    async def transcribe(self, bvid: str, cookies: dict = None) -> Optional[str]:
        """下载音频 → 转录 → 清理。返回全文，失败返回 None。"""
        audio_path = await asyncio.get_event_loop().run_in_executor(
            None, self._download_audio, bvid
        )
        if not audio_path:
            return None

        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._transcribe_audio, audio_path
            )
        finally:
            if os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                except OSError:
                    pass

    # ==================== download ====================

    def _download_audio(self, bvid: str) -> Optional[str]:
        """使用 bilibili-cli 下载音频（完整长度，兼容大会员视频）。

        bilibili-cli 自动从 ~/.bilibili-cli/credential.json 读取认证信息，
        或从浏览器提取 Cookie，无需手动传 cookies。
        """
        # 清理该 bvid 的旧残留文件（包括 yt-dlp 旧格式和 m4a 新格式）
        for old in list(os.listdir(self._output_dir)):
            if old.startswith(bvid) or (old.endswith(".m4a") and bvid in old):
                try:
                    os.remove(os.path.join(self._output_dir, old))
                except OSError:
                    pass

        try:
            result = subprocess.run(
                ["bili", "audio", bvid, "--no-split", "-o", self._output_dir],
                capture_output=True, timeout=600,
                encoding="utf-8", errors="replace",
            )
            if result.returncode != 0:
                logger.error("  [Whisper] bilibili-cli 下载失败: %s",
                             result.stderr[:200] if result.stderr else "unknown")
                return None

            # bilibili-cli 输出文件名为视频标题.m4a，找到并重命名为 bvid.m4a
            m4a_files = [f for f in os.listdir(self._output_dir)
                        if f.endswith(".m4a")]
            if not m4a_files:
                logger.error("  [Whisper] 找不到下载的音频文件")
                return None

            # 取最新修改的文件（防止上次残留干扰）
            m4a_files.sort(key=lambda x: os.path.getmtime(
                os.path.join(self._output_dir, x)), reverse=True)
            src = os.path.join(self._output_dir, m4a_files[0])
            dst = os.path.join(self._output_dir, f"{bvid}.m4a")
            if src != dst:
                os.rename(src, dst)
            logger.info("  [Whisper] 音频下载完成: %s → %s", m4a_files[0],
                        os.path.basename(dst))
            return dst
        except subprocess.TimeoutExpired:
            logger.error("  [Whisper] 音频下载超时 (>10分钟)")
            return None
        except Exception as e:
            logger.error("  [Whisper] 音频下载异常: %s", e)
            return None

    # ==================== transcription ====================

    def _transcribe_audio(self, audio_path: str) -> str:
        from faster_whisper import WhisperModel

        self._load_model()
        lang = self._cfg.whisper_language.strip() or None
        segments, info = self._model.transcribe(
            audio_path,
            language=lang,
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )

        lines = [s.text.strip() for s in segments]
        text = "\n".join(lines)
        logger.info("  [Whisper] 转录完成: %d字 (lang=%s, dur=%.0fs, prob=%.2f)",
                    len(text), info.language, info.duration, info.language_probability)
        return text

    def _load_model(self):
        if self._model is not None:
            return
        from faster_whisper import WhisperModel

        logger.info("  加载 Whisper 模型: %s (%s/%s)",
                    self._cfg.whisper_model_size,
                    self._cfg.whisper_device,
                    self._cfg.whisper_compute_type)
        t0 = time.time()
        self._model = WhisperModel(
            self._cfg.whisper_model_size,
            device=self._cfg.whisper_device,
            compute_type=self._cfg.whisper_compute_type,
        )
        logger.info("  模型加载完成 (%.1fs)", time.time() - t0)
