"""数据存储工具 — 原子写入、进度追踪、JSON 加载"""

import os
import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class DataStorage:
    """文件系统数据存取，支持原子写入和进度持久化"""

    @staticmethod
    def safe_save_json(data: dict, filepath: str) -> None:
        tmp_path = filepath + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, filepath)

    @staticmethod
    def load_json(filepath: str) -> dict:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def load_json_or_default(filepath: str, default: dict = None) -> dict:
        if default is None:
            default = {}
        if not os.path.exists(filepath):
            return default
        try:
            return DataStorage.load_json(filepath)
        except Exception:
            logger.warning("Failed to load %s, returning default", filepath)
            return default

    @staticmethod
    def save_progress(progress: dict, filepath: str) -> None:
        DataStorage.safe_save_json(progress, filepath)

    @staticmethod
    def load_progress(filepath: str) -> dict:
        return DataStorage.load_json_or_default(filepath)

    @staticmethod
    def write_text(filepath: str, content: str) -> None:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

    @staticmethod
    def read_text(filepath: str) -> Optional[str]:
        if not os.path.exists(filepath):
            return None
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
