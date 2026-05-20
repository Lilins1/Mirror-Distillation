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

    # ---------- 阶段启用/禁用开关 ----------
    "ENABLE_STAGE3": True,      # 是否启用第三阶段（AI 深度总结）
    "ENABLE_STAGE4": True,      # 是否启用第四阶段（数据聚合）
    "ENABLE_STAGE5": True,      # 是否启用第五阶段（Skill 生成与质检）

    # ---------- 阶段三专属配置 ----------
    "MIN_VIDEO_DURATION_SECONDS": 120,             # 过滤小于此秒数的视频
    "DEEPSEEK_HIGH_VALUE_THRESHOLD": 20.0,        # 模型选择阈值
    "STAGE3_CONCURRENCY_LIMIT": 1,                # Stage3 并发数
    "PARALLEL_STAGE2_3": True,                    # 是否并行运行 Stage2 和 Stage3
    "STAGE3_POLL_INTERVAL": 30,                  # Stage3 轮询间隔（秒）

    # ---------- 阶段四/五生成配置 ----------
    "TOP_PERCENTILE": 0.5,                        # 高价值节点选取比例
    "PERSONA_DIR": os.path.join("data", "stage4_persona_builder"),
    "SKILL_MD_PATH": os.path.join("data", "stage4_persona_builder", "SKILL.md")
}

# 导入所有子模块（确保文件名与磁盘一致）
import stage1_collector
import stage1_enrich_cif
import stage2_subtitle_extractor
import stage3_summarizer
import stage4_aggregate_to_nuwa
import stage5_merge_research
import stage5_generate_skill
import stage5_quality_check

async def run_pipeline(stage_to_run):
    print("="*70)
    print(" 🚀 [Mirror Distillation] 认知数字分身 - 数据流管线已启动")
    print("="*70)

    # ---------- Stage 1: Collector ----------
    if stage_to_run in ['all', 'daily', '1']:
        print("\n>>> [执行阶段 1] 启动历史数据增量采集...")
        stage1_collector.DEBUG_MODE = PIPELINE_CONFIG["DEBUG_MODE"]
        stage1_collector.DEBUG_MAX_PAGES = PIPELINE_CONFIG["DEBUG_MAX_PAGES"]
        stage1_collector.PROD_MAX_PAGES = PIPELINE_CONFIG["PROD_MAX_PAGES"]
        await stage1_collector.main()

    # ---------- Stage 1.5: Enrich CIF ----------
    if stage_to_run in ['all', 'daily', '1.5']:
        print("\n>>> [执行阶段 1.5] 启动多维数据提纯与 CIF 赋权...")
        stage1_enrich_cif.DEBUG_MODE = PIPELINE_CONFIG["DEBUG_MODE"]
        stage1_enrich_cif.DEBUG_ITEM_LIMIT = PIPELINE_CONFIG["DEBUG_ITEM_LIMIT"]
        stage1_enrich_cif.CONCURRENCY_LIMIT = PIPELINE_CONFIG["CONCURRENCY_LIMIT"]
        await stage1_enrich_cif.main()

    # ---------- Stage 2 & 3: Extraction & Summarization ----------
    if stage_to_run in ['all', 'daily', '2', '3']:
        # 配置注入 Stage2
        stage2_subtitle_extractor.DEBUG_MODE = PIPELINE_CONFIG["DEBUG_MODE"]
        stage2_subtitle_extractor.DEBUG_ITEM_LIMIT = PIPELINE_CONFIG["DEBUG_ITEM_LIMIT"]
        stage2_subtitle_extractor.CONCURRENCY_LIMIT = PIPELINE_CONFIG["CONCURRENCY_LIMIT"]
        stage2_subtitle_extractor.ENABLE_SPONSOR_BLOCK = PIPELINE_CONFIG["ENABLE_SPONSOR_BLOCK"]

        # 配置注入 Stage3（仅当 ENABLE_STAGE3 为 True 时注入）
        if PIPELINE_CONFIG["ENABLE_STAGE3"]:
            stage3_summarizer.DEBUG_MODE = PIPELINE_CONFIG["DEBUG_MODE"]
            stage3_summarizer.DEBUG_ITEM_LIMIT = PIPELINE_CONFIG["DEBUG_ITEM_LIMIT"]
            stage3_summarizer.CONCURRENCY_LIMIT = PIPELINE_CONFIG["STAGE3_CONCURRENCY_LIMIT"]
            stage3_summarizer.MIN_VIDEO_DURATION_SECONDS = PIPELINE_CONFIG["MIN_VIDEO_DURATION_SECONDS"]
            stage3_summarizer.HIGH_VALUE_THRESHOLD = PIPELINE_CONFIG["DEEPSEEK_HIGH_VALUE_THRESHOLD"]
            stage3_summarizer.POLL_INTERVAL = PIPELINE_CONFIG.get("STAGE3_POLL_INTERVAL", 30)

        # 根据 ENABLE_STAGE3 和并行设置决定执行模式
        if PIPELINE_CONFIG["ENABLE_STAGE3"] and stage_to_run in ['all', 'daily'] and PIPELINE_CONFIG.get("PARALLEL_STAGE2_3", True):
            # ---- 并行模式 ----
            print("\n>>> [并行模式] 同时启动阶段2（字幕提取）与阶段3（AI总结）...")
            stage2_done = asyncio.Event()
            await asyncio.gather(
                stage2_subtitle_extractor.main_parallel(stage2_done),
                stage3_summarizer.main_parallel(stage2_done)
            )
        else:
            # ---- 串行模式 ----
            if stage_to_run in ['all', 'daily', '2']:
                print("\n>>> [执行阶段 2] 启动多模态字幕抽取与广告清洗...")
                await stage2_subtitle_extractor.main()
            if stage_to_run in ['all', 'daily', '3'] and PIPELINE_CONFIG["ENABLE_STAGE3"]:
                print("\n>>> [执行阶段 3] 启动 AI 深度认知蒸馏 (DeepSeek)...")
                await stage3_summarizer.main()

    # ---------- Stage 4: Aggregate to Nuwa ----------
    if stage_to_run in ['all', '4'] and PIPELINE_CONFIG["ENABLE_STAGE4"]:
        print("\n>>> [执行阶段 4] 启动数据变压器，生成 6 维认知报告...")
        stage4_aggregate_to_nuwa.TOP_PERCENTILE = PIPELINE_CONFIG["TOP_PERCENTILE"]
        await stage4_aggregate_to_nuwa.main()

    # ---------- Stage 5: Skill Generation & Validation ----------
    if stage_to_run in ['all', '5'] and PIPELINE_CONFIG["ENABLE_STAGE5"]:
        print("\n>>> [执行阶段 5.1] 合并调研结果并进行 Review...")
        stage5_merge_research.run_merge(PIPELINE_CONFIG["PERSONA_DIR"])

        print("\n>>> [执行阶段 5.2] 调用 LLM 生成最终的 SKILL.md...")
        await stage5_generate_skill.main()

        print("\n>>> [执行阶段 5.3] 启动自动化质量检测...")
        stage5_quality_check.run_check(PIPELINE_CONFIG["SKILL_MD_PATH"])

    print("\n" + "="*70)
    print(" 🎉 [ALL DONE] Mirror 蒸馏管线指定任务执行完毕！")
    if PIPELINE_CONFIG["ENABLE_STAGE5"] and stage_to_run in ['all', '5']:
        print(f" 📂 最终认知镜像: {PIPELINE_CONFIG['SKILL_MD_PATH']}")
    print("="*70)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Mirror 蒸馏认知管线总控台")
    # ✅ 在 choices 中新增了 'daily' 选项
    parser.add_argument('--stage', type=str, choices=['all', 'daily', '1', '1.5', '2', '3', '4', '5'], default='all',
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
    except Exception as e:
        print(f"\n[FATAL] 管线运行异常: {e}")
        import traceback
        traceback.print_exc()