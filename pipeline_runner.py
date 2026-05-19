import os
import sys
import asyncio
import argparse

# ==========================================
# 统一全局配置中心 (Central Configuration)
# ==========================================
PIPELINE_CONFIG = {
    "DEBUG_MODE": True,               # 调试模式（覆盖所有子模块）
    "DEBUG_ITEM_LIMIT": 10,            # 测试模式下的处理上限 (提纯与字幕阶段)
    "DEBUG_MAX_PAGES": 2,              # 测试模式下抓取历史记录的页数
    "PROD_MAX_PAGES": 200,             # 正式抓取时的最大历史页数
    "CONCURRENCY_LIMIT": 1,            # 全局并发限制（防止触发412风控）
    "ENABLE_LOCAL_WHISPER": False,     # 字幕阶段：是否开启本地 ASR 极限兜底
    "WHISPER_MODEL_SIZE": "small"      # Whisper 模型精度 (tiny/base/small/medium/large)
}

# 导入三个子模块
import stage1_collector
import stage1_enrich_cif
import stage2_subtitle_extractor

async def run_pipeline(stage_to_run):
    print("="*70)
    print(" 🚀 [Mirror Distillation] 认知数字分身 - 数据流管线已启动")
    print("="*70)
    
    # ---------------------------------------------------------
    # 阶段一：历史记录与增量基础数据采集
    # ---------------------------------------------------------
    if stage_to_run in ['all', '1']:
        print("\n>>> [执行阶段 1] 启动历史数据增量采集 (Collector)...")
        stage1_collector.DEBUG_MODE = PIPELINE_CONFIG["DEBUG_MODE"]
        stage1_collector.DEBUG_MAX_PAGES = PIPELINE_CONFIG["DEBUG_MAX_PAGES"]
        stage1_collector.PROD_MAX_PAGES = PIPELINE_CONFIG["PROD_MAX_PAGES"]
        
        await stage1_collector.main()

    # ---------------------------------------------------------
    # 阶段一点五：数据提纯与 CIF 赋权 (分类、标签、互动特征)
    # ---------------------------------------------------------
    if stage_to_run in ['all', '1.5']:
        print("\n>>> [执行阶段 1.5] 启动多维数据提纯与 CIF 赋权 (Enrich)...")
        stage1_enrich_cif.DEBUG_MODE = PIPELINE_CONFIG["DEBUG_MODE"]
        stage1_enrich_cif.DEBUG_ITEM_LIMIT = PIPELINE_CONFIG["DEBUG_ITEM_LIMIT"]
        stage1_enrich_cif.CONCURRENCY_LIMIT = PIPELINE_CONFIG["CONCURRENCY_LIMIT"]
        
        await stage1_enrich_cif.main()

    # ---------------------------------------------------------
    # 阶段二：多模态字幕抽取与恰饭广告清洗
    # ---------------------------------------------------------
    if stage_to_run in ['all', '2']:
        print("\n>>> [执行阶段 2] 启动多模态字幕抽取与广告清洗 (Subtitle Extractor)...")
        stage2_subtitle_extractor.DEBUG_MODE = PIPELINE_CONFIG["DEBUG_MODE"]
        stage2_subtitle_extractor.DEBUG_ITEM_LIMIT = PIPELINE_CONFIG["DEBUG_ITEM_LIMIT"]
        stage2_subtitle_extractor.CONCURRENCY_LIMIT = PIPELINE_CONFIG["CONCURRENCY_LIMIT"]
        stage2_subtitle_extractor.ENABLE_LOCAL_WHISPER = PIPELINE_CONFIG["ENABLE_LOCAL_WHISPER"]
        stage2_subtitle_extractor.WHISPER_MODEL_SIZE = PIPELINE_CONFIG["WHISPER_MODEL_SIZE"]
        
        await stage2_subtitle_extractor.main()

    print("\n" + "="*70)
    print(" 🎉 [ALL DONE] Mirror 蒸馏管线指定任务执行完毕！")
    print(" 📂 最终沉淀的高净值 JSON 知识颗粒已存放在: data/stage2_subtitles/parsed_videos/")
    print("="*70)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Mirror 蒸馏认知管线总控台")
    parser.add_argument('--stage', type=str, choices=['all', '1', '1.5', '2'], default='all', 
                        help="选择要执行的流水线阶段 (默认: all 执行全流程)")
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