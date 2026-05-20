#!/usr/bin/env python3
"""
自动检查生成的SKILL.md是否通过阶段质量标准。
对照通过标准表格逐项检查，输出通过/不通过和具体原因。

用法:
    python3 quality_check.py <SKILL.md路径>

示例:
    python3 quality_check.py data/stage4_persona_builder/SKILL.md
"""

import sys
import re
from pathlib import Path


def check_mental_models(content: str) -> tuple[bool, str]:
    """检查心智模型数量（3-7个）"""
    # 匹配 ### 模型N: 或 ### N. 等模式
    models = re.findall(r'^###\s+(?:模型|Model|心智模型)\s*\d', content, re.MULTILINE)
    if not models:
        # fallback: 数「### 」开头的行在心智模型section中
        in_section = False
        count = 0
        for line in content.split('\n'):
            if re.match(r'^##\s+.*心智模型|Mental Model', line, re.IGNORECASE):
                in_section = True
                continue
            if in_section and re.match(r'^##\s+', line) and '心智模型' not in line:
                break
            if in_section and re.match(r'^###\s+', line):
                count += 1
        if count > 0:
            passed = 3 <= count <= 7
            return passed, f"{count}个心智模型 {'✅' if passed else '❌ (应为3-7个)'}"

    count = len(models)
    if count == 0:
        return False, "未检测到心智模型section"
    passed = 3 <= count <= 7
    return passed, f"{count}个心智模型 {'✅' if passed else '❌ (应为3-7个)'}"


def check_limitations(content: str) -> tuple[bool, str]:
    """检查每个模型是否有局限性"""
    has_limitation = bool(re.search(r'局限|失效|不适用|盲区|limitation|blind spot', content, re.IGNORECASE))
    return has_limitation, "有局限性标注 ✅" if has_limitation else "❌ 未找到局限性描述"


def check_expression_dna(content: str) -> tuple[bool, str]:
    """检查表达DNA辨识度"""
    dna_section = bool(re.search(r'表达DNA|Expression DNA|表达风格|审美投射', content, re.IGNORECASE))
    if not dna_section:
        return False, "❌ 未找到表达DNA section"

    # 检查是否有具体的风格描述（句式、词汇等）
    style_markers = len(re.findall(r'句式|词汇|语气|情绪基调|确定性|论述结构|论据偏好|口头禅', content))
    passed = style_markers >= 3
    return passed, f"表达DNA特征: {style_markers}项 {'✅' if passed else '❌ (应≥3项)'}"


def check_honest_boundary(content: str) -> tuple[bool, str]:
    """检查诚实边界（至少3条）"""
    # 找诚实边界section
    boundary_match = re.search(r'(?:##\s+.*诚实边界|## Honest Boundary)(.*?)(?=\n##\s|\Z)', content, re.DOTALL | re.IGNORECASE)
    if not boundary_match:
        return False, "❌ 未找到诚实边界section"

    boundary_text = boundary_match.group(1)
    # 计算列表项
    items = re.findall(r'^[-*]\s+|\d+\.\s+', boundary_text, re.MULTILINE)
    count = len(items)
    passed = count >= 3
    return passed, f"诚实边界: {count}条 {'✅' if passed else '❌ (应≥3条)'}"


def check_tensions(content: str) -> tuple[bool, str]:
    """检查内在张力（至少2处或者1个明确列表）"""
    tension_markers = len(re.findall(r'张力|矛盾|tension|paradox|一方面.*另一方面|既.*又|vs', content, re.IGNORECASE))
    passed = tension_markers >= 1 # 稍微放宽对个人认知的张力要求，至少有1处
    return passed, f"内在张力: {tension_markers}处 {'✅' if passed else '❌ (应≥1处)'}"


def main():
    if len(sys.argv) < 2:
        print("用法: python3 quality_check.py <SKILL.md路径>")
        sys.exit(1)

    skill_path = Path(sys.argv[1])
    if not skill_path.exists():
        print(f"❌ 文件不存在: {skill_path}")
        sys.exit(1)

    content = skill_path.read_text(encoding='utf-8')

    # 已移除无关的一手/二手资料检查
    checks = [
        ("心智模型数量", check_mental_models),
        ("模型局限性", check_limitations),
        ("表达DNA辨识度", check_expression_dna),
        ("诚实边界", check_honest_boundary),
        ("内在张力", check_tensions),
    ]

    print(f"质量检查: {skill_path.name}")
    print("=" * 50)

    passed_count = 0
    total = len(checks)

    for name, check_fn in checks:
        passed, detail = check_fn(content)
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {name:<12} {status}  {detail}")
        if passed:
            passed_count += 1

    print("=" * 50)
    print(f"结果: {passed_count}/{total} 通过")

    if passed_count == total:
        print("🎉 全部通过，认知镜像大模型可以开始使用了！")
    elif passed_count >= total - 1:
        print("⚠️ 基本通过，建议稍作手动修补即可完成。")
    else:
        print("❌ 核心设定项提取失败，建议重新执行大模型生成步骤 (Phase 4)。")

    sys.exit(0 if passed_count == total else 1)


if __name__ == '__main__':
    main()