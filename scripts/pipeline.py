"""管线总控台 — 统一调度所有阶段，支持串行、并行、daily 模式"""

import os
import sys
import asyncio
import argparse
import logging
from datetime import datetime

from .config import PipelineConfig
from .collector import Stage1Collector
from .enricher import Stage1Enricher
from .extractor import Stage2Extractor
from .summarizer import Stage3Summarizer
from .aggregator import Stage4Aggregator
from .skill_generator import SkillGenerator
from .research_merger import ResearchMerger
from .quality_checker import QualityChecker
from .up_persona import UpPersonaPipeline

logger = logging.getLogger(__name__)


class PipelineRunner:
    """Mirror 蒸馏管线总调度器"""

    def __init__(self, config: PipelineConfig = None):
        self._cfg = config or PipelineConfig()

    # ==================== main entry ====================

    async def run(self, stage: str) -> None:
        self._print_banner()
        cfg = self._cfg

        # --- Stage 1 ---
        if stage in ("all", "daily", "1"):
            logger.info("[Stage 1] 历史采集...")
            await Stage1Collector(cfg).run()

        # --- Stage 1.5 ---
        if stage in ("all", "daily", "1.5"):
            logger.info("[Stage 1.5] CIF 提纯...")
            await Stage1Enricher(cfg).run()

        # --- Stage 2 & 3 ---
        if stage in ("all", "daily", "2", "3"):
            extractor = Stage2Extractor(cfg)

            if cfg.enable_stage3 and stage in ("all", "daily") and cfg.parallel_stage2_3:
                logger.info("[并行] Stage 2 + Stage 3...")
                done = asyncio.Event()
                await asyncio.gather(
                    extractor.run_parallel(done),
                    Stage3Summarizer(cfg).run_parallel(done),
                )
            else:
                if stage in ("all", "daily", "2"):
                    logger.info("[Stage 2] 字幕提取...")
                    await extractor.run()
                if stage in ("all", "daily", "3") and cfg.enable_stage3:
                    logger.info("[Stage 3] AI 蒸馏...")
                    await Stage3Summarizer(cfg).run()

        # --- Stage 4 ---
        if stage in ("all", "4") and cfg.enable_stage4:
            logger.info("[Stage 4] 研究报告生成...")
            await Stage4Aggregator(cfg).run()

        # --- Stage 5 ---
        if stage in ("all", "5") and cfg.enable_stage5:
            logger.info("[Stage 5.1] 研究合并 Review...")
            ResearchMerger(cfg).run()

            logger.info("[Stage 5.2] SKILL.md 生成...")
            await SkillGenerator(cfg).run()

            logger.info("[Stage 5.3] 质量检查...")
            QualityChecker(cfg).run_default()

        # --- Stage UP: UP Persona ---
        if stage in ("all", "up") and cfg.enable_up_persona:
            logger.info("[Stage UP] UP 主人物 Skill 管线...")
            await UpPersonaPipeline(cfg).run()

        self._print_done(stage)

    # ==================== helpers ====================

    def _print_banner(self):
        logger.info("=" * 60)
        logger.info("Mirror Distillation 认知数字分身管线")
        logger.info("=" * 60)

    def _print_done(self, stage: str):
        logger.info("=" * 60)
        logger.info("管线执行完毕")
        if stage in ("all", "5") and self._cfg.enable_stage5:
            logger.info("最终产物: %s", self._cfg.skill_output_path)
        logger.info("=" * 60)

    # ==================== CLI ====================

    @staticmethod
    def setup_platform():
        if sys.platform == "win32":
            os.system("chcp 65001")
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    @staticmethod
    def _setup_logging(debug: bool = False, log_dir: str = None):
        level = logging.DEBUG if debug else logging.INFO
        console_fmt = ("%(asctime)s [%(levelname)-5s] %(name)-22s %(message)s"
                       if debug else "[%(levelname)-5s] %(message)s")
        file_fmt = "%(asctime)s [%(levelname)-5s] %(name)-22s %(message)s"

        handlers = [logging.StreamHandler()]
        handlers[0].setFormatter(logging.Formatter(console_fmt, datefmt="%H:%M:%S"))

        log_path = ""
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, f"run_{datetime.now():%Y%m%d_%H%M%S}.log")
            fh = logging.FileHandler(log_path, encoding="utf-8")
            fh.setFormatter(logging.Formatter(file_fmt, datefmt="%Y-%m-%d %H:%M:%S"))
            handlers.append(fh)

        logging.basicConfig(level=level, handlers=handlers, force=True)

        # suppress noisy third-party loggers
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("openai").setLevel(logging.WARNING)
        logging.getLogger("bilibili_api").setLevel(logging.WARNING)

        if log_path:
            logging.getLogger(__name__).info("本次运行日志: %s", log_path)

    @classmethod
    def main(cls):
        cls.setup_platform()

        parser = argparse.ArgumentParser(description="Mirror 蒸馏管线总控台")
        parser.add_argument("--stage", choices=["all", "daily", "1", "1.5", "2", "3", "4", "5", "up"],
                            default="all", help="选择阶段 (默认: all, up=UP人物Skill)")
        parser.add_argument("--debug", action="store_true", help="启用 DEBUG 模式")
        args = parser.parse_args()

        config = PipelineConfig()
        if args.debug:
            config.debug_mode = True

        cls._setup_logging(debug=args.debug, log_dir=config.log_dir)
        if args.debug:
            logger.info("[DEBUG] 已启用")

        runner = cls(config)
        try:
            asyncio.run(runner.run(args.stage))
        except KeyboardInterrupt:
            logger.warning("收到中断信号，管线已挂起")
        except Exception as e:
            logger.exception("管线异常: %s", e)
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    PipelineRunner.main()
