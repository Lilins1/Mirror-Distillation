#!/usr/bin/env python3
"""
自动检查生成的SKILL.md是否通过阶段质量标准。
支持独立运行（默认扫描当前生成目录）或代码 import 调用。

用法:
    python3 stage4_quality_check.py [可选: SKILL.md路径]
"""

import sys
import re
from pathlib import Path

def check_mental_models(content: str) -> tuple[bool, str]:
    models = re.findall(r'^###\s+(?:模型|Model|心智模型)\s*\d', content, re.MULTILINE)
    if not models:
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
    has_limitation = bool(re.search(r'局限|失效|不适用|盲区|limitation|blind spot', content, re.IGNORECASE))
    return has_limitation, "有局限性标注 ✅" if has_limitation else "❌ 未找到局限性描述"

def check_expression_dna(content: str) -> tuple[bool, str]:
    dna_section = bool(re.search(r'表达DNA|Expression DNA|表达风格|审美投射', content, re.IGNORECASE))
    if not dna_section:
        return False, "❌ 未找到表达DNA section"
    style_markers = len(re.findall(r'句式|词汇|语气|情绪基调|确定性|论述结构|论据偏好|口头禅', content))
    passed = style_markers >= 3
    return passed, f"表达DNA特征: {style_markers}项 {'✅' if passed else '❌ (应≥3项)'}"

def check_honest_boundary(content: str) -> tuple[bool, str]:
    boundary_match = re.search(r'(?:##\s+.*诚实边界|## Honest Boundary)(.*?)(?=\n##\s|\Z)', content, re.DOTALL | re.IGNORECASE)
    if not boundary_match:
        return False, "❌ 未找到诚实边界section"
    boundary_text = boundary_match.group(1)
    items = re.findall(r'^[-*]\s+|\d+\.\s+', boundary_text, re.MULTILINE)
    count = len(items)
    passed = count >= 3
    return passed, f"诚实边界: {count}条 {'✅' if passed else '❌ (应≥3条)'}"

def check_tensions(content: str) -> tuple[bool, str]:
    tension_markers = len(re.findall(r'张力|矛盾|tension|paradox|一方面.*另一方面|既.*又|vs', content, re.IGNORECASE))
    passed = tension_markers >= 1 
    return passed, f"内在张力: {tension_markers}处 {'✅' if passed else '❌ (应≥1处)'}"

def run_check(skill_path_str: str):
    """核心执行函数，方便外部调用"""
    skill_path = Path(skill_path_str)
    if not skill_path.exists():
        print(f"❌ 文件不存在: {skill_path}")
        print("💡 请确保大模型已经为您生成了最终的 SKILL.md 文件。")
        return False

    content = skill_path.read_text(encoding='utf-8')

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
    
    return passed_count == total

def main():
    # 提供友好的默认路径
    default_path = "data/stage4_persona_builder/SKILL.md"
    skill_path = sys.argv[1] if len(sys.argv) > 1 else default_path
    
    success = run_check(skill_path)
    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()