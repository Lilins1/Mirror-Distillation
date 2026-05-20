import os
import sys
import asyncio
import argparse

# ==========================================
# 统一全局配置中心 (Central Configuration)
# ==========================================
PIPELINE_CONFIG = {
    "DEBUG_MODE": False,
    "DEBUG_ITEM_LIMIT": 10,
    "DEBUG_MAX_PAGES": 2,
    "PROD_MAX_PAGES": 200,
    "CONCURRENCY_LIMIT": 1,
    "ENABLE_SPONSOR_BLOCK": True,
    "ENABLE_LOCAL_WHISPER": False,
    "WHISPER_MODEL_SIZE": "small",
    # ---------- 阶段三专属配置 ----------
    "ENABLE_STAGE3": False,                        # 是否启用第三阶段总结
    "MIN_VIDEO_DURATION_SECONDS": 60,             # 过滤小于60秒的视频
    "DEEPSEEK_HIGH_VALUE_THRESHOLD": 6.0,        # 模型选择阈值
    "STAGE3_CONCURRENCY_LIMIT": 1                 # 可单独设置总结并发（一般与全局一致）
}

# 导入四个子模块（新增 stage3_summarizer）
import stage1_collector
import stage1_enrich_cif
import stage2_subtitle_extractor
import stage3_summarizer      # 新增

async def run_pipeline(stage_to_run):
    print("="*70)
    print(" 🚀 [Mirror Distillation] 认知数字分身 - 数据流管线已启动")
    print("="*70)

    if stage_to_run in ['all', '1']:
        print("\n>>> [执行阶段 1] 启动历史数据增量采集...")
        stage1_collector.DEBUG_MODE = PIPELINE_CONFIG["DEBUG_MODE"]
        stage1_collector.DEBUG_MAX_PAGES = PIPELINE_CONFIG["DEBUG_MAX_PAGES"]
        stage1_collector.PROD_MAX_PAGES = PIPELINE_CONFIG["PROD_MAX_PAGES"]
        await stage1_collector.main()

    if stage_to_run in ['all', '1.5']:
        print("\n>>> [执行阶段 1.5] 启动多维数据提纯与 CIF 赋权...")
        stage1_enrich_cif.DEBUG_MODE = PIPELINE_CONFIG["DEBUG_MODE"]
        stage1_enrich_cif.DEBUG_ITEM_LIMIT = PIPELINE_CONFIG["DEBUG_ITEM_LIMIT"]
        stage1_enrich_cif.CONCURRENCY_LIMIT = PIPELINE_CONFIG["CONCURRENCY_LIMIT"]
        await stage1_enrich_cif.main()

    if stage_to_run in ['all', '2']:
        print("\n>>> [执行阶段 2] 启动多模态字幕抽取与广告清洗...")
        stage2_subtitle_extractor.DEBUG_MODE = PIPELINE_CONFIG["DEBUG_MODE"]
        stage2_subtitle_extractor.DEBUG_ITEM_LIMIT = PIPELINE_CONFIG["DEBUG_ITEM_LIMIT"]
        stage2_subtitle_extractor.CONCURRENCY_LIMIT = PIPELINE_CONFIG["CONCURRENCY_LIMIT"]
        stage2_subtitle_extractor.ENABLE_LOCAL_WHISPER = PIPELINE_CONFIG["ENABLE_LOCAL_WHISPER"]
        stage2_subtitle_extractor.WHISPER_MODEL_SIZE = PIPELINE_CONFIG["WHISPER_MODEL_SIZE"]
        await stage2_subtitle_extractor.main()

    if stage_to_run in ['all', '3'] and PIPELINE_CONFIG["ENABLE_STAGE3"]:
        print("\n>>> [执行阶段 3] 启动 AI 深度认知蒸馏 (DeepSeek)...")
        stage3_summarizer.DEBUG_MODE = PIPELINE_CONFIG["DEBUG_MODE"]
        stage3_summarizer.DEBUG_ITEM_LIMIT = PIPELINE_CONFIG["DEBUG_ITEM_LIMIT"]
        stage3_summarizer.CONCURRENCY_LIMIT = PIPELINE_CONFIG["STAGE3_CONCURRENCY_LIMIT"]
        stage3_summarizer.MIN_VIDEO_DURATION_SECONDS = PIPELINE_CONFIG["MIN_VIDEO_DURATION_SECONDS"]
        stage3_summarizer.HIGH_VALUE_THRESHOLD = PIPELINE_CONFIG["DEEPSEEK_HIGH_VALUE_THRESHOLD"]
        await stage3_summarizer.main()

    print("\n" + "="*70)
    print(" 🎉 [ALL DONE] Mirror 蒸馏管线指定任务执行完毕！")
    print(" 📂 最终知识颗粒输出: data/stage3_summaries/")
    print("="*70)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Mirror 蒸馏认知管线总控台")
    parser.add_argument('--stage', type=str, choices=['all', '1', '1.5', '2', '3'], default='all',
                        help="选择要执行的流水线阶段 (默认: all)")
    parser.add_argument('--debug', action='store_true',
                        help="挂载此标志以激活全局 DEBUG 测试模式")
    args = parser.parse_args()

    if args.debug:
        PIPELINE_CONFIG["DEBUG_MODE"] = True
        print("[!] 已通过命令行强制挂载 DEBUG 模式")

    if sys.platform == 'win32':
        os.system('chcp 65001')
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        asyncio.run(run_pipeline(args.stage))
    except KeyboardInterrupt:
        print("\n[!] 收到终端中止信号，管线已紧急挂起。")