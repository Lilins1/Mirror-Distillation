"""Stage 5.2: Skill 生成器 — 基于研究报告单次直渲染生成 SKILL.md"""

import logging
from datetime import datetime
from typing import Optional

from openai import AsyncOpenAI

from .config import PipelineConfig, DeepSeekConfig
from .storage import DataStorage

logger = logging.getLogger(__name__)


class SkillGenerator:
    """使用 DeepSeek 大模型将6维研究报告渲染为 SKILL.md"""

    RESEARCH_FILES = [
        "01-core-consumption.md",
        "02-value-resonances.md",
        "03-expression-dna.md",
        "04-boundaries-rejections.md",
        "05-decision-heuristics.md",
        "06-timeline.md",
    ]

    def __init__(self, config: PipelineConfig):
        self._cfg = config
        self._storage = DataStorage()

    async def run(self) -> None:
        ds_config = DeepSeekConfig.from_file(self._cfg.deepseek_config_path)

        # 读取所有输入
        research = self._load_research()
        if not research:
            logger.error("无有效调研报告数据")
            return

        template = self._storage.read_text(self._cfg.template_path)
        if not template:
            logger.error("找不到模板: %s", self._cfg.template_path)
            return

        framework = self._storage.read_text(self._cfg.framework_path)
        if not framework:
            logger.error("找不到方法论文档: %s", self._cfg.framework_path)
            return

        skill_md = await self._generate(ds_config, framework, research, template)
        if skill_md is None:
            logger.error("Skill 生成失败")
            return

        run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._write_output(skill_md, run_ts)
        logger.info("SKILL.md 已生成: %s", self._cfg.skill_output_path)

    # ==================== private ====================

    def _load_research(self) -> str:
        import os
        content = ""
        for fn in self.RESEARCH_FILES:
            fp = os.path.join(self._cfg.research_dir, fn)
            text = self._storage.read_text(fp)
            if text:
                content += f"\n\n--- FILE: {fn} ---\n\n{text}"
        return content

    async def _generate(self, ds_config: DeepSeekConfig,
                         framework: str, research: str, template: str) -> Optional[str]:
        system = (
            "你是 Mirror Distillation 的认知建模与渲染引擎。"
            "你的任务是基于完整研究语料直接生成最终 SKILL.md。"
            "严格遵守：只依据研究语料与方法论；禁止空泛套话；"
            "禁止将单作者观点等同于用户心智模型；禁止补写证据外结论；"
            "证据不足时保留谨慎表达或降级。"
        )

        user = f"""方法论文档（硬约束）：
---
{framework}
---

研究语料：
---
{research}
---

模板：
---
{template}
---

硬性要求：
1. 直接输出最终 Markdown，不要 JSON、提纲或思维链。
2. 核心心智模型只保留跨域复现+可决策应用+高价值证据支撑的候选。
3. 筛选后不足3个不强凑；每个模型须含逻辑内核、证据锚点、跨域复现、映射来源、决策应用、认知局限。
4. 决策启发式必须是默认动作规则，非空泛口号。
5. 表达 DNA 只提炼多来源交集，不复刻单作者口头禅。
6. 候选排斥信息须明示为镜像反推而非显式事实。
7. 时间证据不足必须降级为"近期关注切片"。
8. 模板方括号与示例仅为结构提示，不构成推荐答案。
9. 输出纯 Markdown，不加解释。

生成策略：先在脑中证据筛选，再按模板渲染。
结论若无法满足方法论门槛，放弃而非补写。
结果应像"可调用认知操作系统"，非"聪明人风格简介"。
"""

        logger.info("调用 %s 直渲染 SKILL.md...", self._cfg.stage5_model)
        client = AsyncOpenAI(api_key=ds_config.api_key, base_url=ds_config.base_url)
        try:
            resp = await client.chat.completions.create(
                model=self._cfg.stage5_model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.2,
                max_tokens=32000,
            )
            raw = resp.choices[0].message.content or ""
            return self._strip_fences(raw)
        except Exception as e:
            logger.error("LLM 调用失败: %s", e)
            return None

    def _write_output(self, skill_md: str, run_ts: str):
        # latest
        self._storage.write_text(self._cfg.skill_output_path, skill_md)
        # archive
        arch = f"{self._cfg.history_dir}/{run_ts}/SKILL.md"
        self._storage.write_text(arch, skill_md)

    @staticmethod
    def _strip_fences(text: str) -> str:
        t = text.strip()
        for prefix in ("```json", "```markdown", "```"):
            if t.startswith(prefix):
                t = t[len(prefix):].strip()
        if t.endswith("```"):
            t = t[:-3].strip()
        return t
