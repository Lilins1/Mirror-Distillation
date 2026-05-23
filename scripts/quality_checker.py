"""Stage 5.3: 质量检查器 — 验证 SKILL.md 是否通过5个质量门禁"""

import re
import logging
from pathlib import Path

from .config import PipelineConfig
from .storage import DataStorage

logger = logging.getLogger(__name__)


class QualityChecker:
    """检查生成的 SKILL.md 是否达到质量标准"""

    def __init__(self, config: PipelineConfig):
        self._cfg = config

    @staticmethod
    def check(path: str) -> bool:
        """对给定文件执行全部5项检查，返回是否全部通过"""
        import os
        if not os.path.exists(path):
            logger.error("文件不存在: %s", path)
            return False

        content = DataStorage.read_text(path)
        if not content:
            return False

        checks = [
            ("心智模型数量", QualityChecker._check_mental_models),
            ("模型局限性", QualityChecker._check_limitations),
            ("表达DNA辨识度", QualityChecker._check_expression_dna),
            ("诚实边界", QualityChecker._check_honest_boundary),
            ("内在张力", QualityChecker._check_tensions),
        ]

        print(f"质量检查: {Path(path).name}")
        print("=" * 50)
        passed_count = 0

        for name, fn in checks:
            ok, detail = fn(content)
            status = "✅ PASS" if ok else "❌ FAIL"
            print(f"  {name:<12} {status}  {detail}")
            if ok:
                passed_count += 1

        print("=" * 50)
        print(f"结果: {passed_count}/{len(checks)} 通过")

        if passed_count == len(checks):
            print("全部通过")
        elif passed_count >= len(checks) - 1:
            print("基本通过，建议手动修补")
        else:
            print("核心设定提取失败，建议重新生成")
        return passed_count == len(checks)

    def run_default(self):
        """使用默认路径执行检查"""
        self.check(self._cfg.skill_output_path)

    # ==================== check functions ====================

    @staticmethod
    def _check_mental_models(content: str) -> tuple:
        models = re.findall(r"^###\s+(?:模型|Model|心智模型)\s*\d", content, re.MULTILINE)
        if not models:
            in_section = False
            count = 0
            for line in content.split("\n"):
                if re.match(r"^##\s+.*心智模型|Mental Model", line, re.IGNORECASE):
                    in_section = True
                    continue
                if in_section and re.match(r"^##\s+", line) and "心智模型" not in line:
                    break
                if in_section and re.match(r"^###\s+", line):
                    count += 1
            if count > 0:
                ok = 3 <= count <= 7
                return ok, f"{count}个心智模型 {'✅' if ok else '❌ (应为3-7个)'}"
        count = len(models)
        if count == 0:
            return False, "未检测到心智模型"
        ok = 3 <= count <= 7
        return ok, f"{count}个心智模型 {'✅' if ok else '❌ (应为3-7个)'}"

    @staticmethod
    def _check_limitations(content: str) -> tuple:
        ok = bool(re.search(r"局限|失效|不适用|盲区|limitation|blind spot", content, re.IGNORECASE))
        return ok, "有局限性标注 ✅" if ok else "❌ 未找到局限性描述"

    @staticmethod
    def _check_expression_dna(content: str) -> tuple:
        if not re.search(r"表达DNA|Expression DNA|表达风格|审美投射", content, re.IGNORECASE):
            return False, "❌ 未找到表达DNA"
        markers = len(re.findall(r"句式|词汇|语气|情绪基调|确定性|论述结构|论据偏好|口头禅", content))
        ok = markers >= 3
        return ok, f"表达DNA特征: {markers}项 {'✅' if ok else '❌ (应≥3项)'}"

    @staticmethod
    def _check_honest_boundary(content: str) -> tuple:
        m = re.search(r"(?:##\s+.*诚实边界|## Honest Boundary)(.*?)(?=\n##\s|\Z)", content, re.DOTALL | re.IGNORECASE)
        if not m:
            return False, "❌ 未找到诚实边界"
        items = re.findall(r"^[-*]\s+|\d+\.\s+", m.group(1), re.MULTILINE)
        ok = len(items) >= 3
        return ok, f"诚实边界: {len(items)}条 {'✅' if ok else '❌ (应≥3条)'}"

    @staticmethod
    def _check_tensions(content: str) -> tuple:
        markers = len(re.findall(r"张力|矛盾|tension|paradox|一方面.*另一方面|既.*又|vs", content, re.IGNORECASE))
        ok = markers >= 1
        return ok, f"内在张力: {markers}处 {'✅' if ok else '❌ (应≥1处)'}"
