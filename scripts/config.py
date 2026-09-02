"""统一配置中心 - 所有管线参数集中管理，通过依赖注入传递"""

import os
import json
from dataclasses import dataclass, field


@dataclass
class DeepSeekConfig:
    api_key: str = ""
    base_url: str = "https://api.deepseek.com/v1"

    @classmethod
    def from_file(cls, path: str) -> "DeepSeekConfig":
        if not os.path.exists(path):
            raise FileNotFoundError(f"DeepSeek config not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        api_key = data.get("api_key", "").strip()
        if not api_key:
            raise ValueError("DeepSeek config missing api_key")
        base_url = data.get("base_url", "").strip() or "https://api.deepseek.com/v1"
        return cls(api_key=api_key, base_url=base_url)


@dataclass
class PipelineConfig:
    # ---- Paths ----
    data_dir: str = "data"
    account_dir: str = field(init=False)
    stage1_dir: str = field(init=False)
    enrich_dir: str = field(init=False)
    stage2_dir: str = field(init=False)
    subtitles_dir: str = field(init=False)
    stage3_dir: str = field(init=False)
    persona_dir: str = field(init=False)
    research_dir: str = field(init=False)
    history_dir: str = field(init=False)
    system_dir: str = field(init=False)
    log_dir: str = field(init=False)
    credential_path: str = field(init=False)
    guest_credential_path: str = field(init=False)
    deepseek_config_path: str = field(init=False)
    master_index_file: str = field(init=False)
    master_enriched_file: str = field(init=False)
    subtitle_progress_file: str = field(init=False)
    stage3_progress_file: str = field(init=False)
    enrich_progress_file: str = field(init=False)
    skill_output_path: str = field(init=False)
    template_path: str = field(init=False)
    framework_path: str = field(init=False)

    # ---- Debug ----
    debug_mode: bool = False
    debug_item_limit: int = 10
    debug_max_pages: int = 2

    # ---- Stage 1 ----
    prod_max_pages: int = 200
    deep_scan_interval: int = 3 * 24 * 3600

    # ---- Concurrency ----
    concurrency_limit: int = 1

    # ---- Stage 2 ----
    enable_sponsor_block: bool = True
    sponsor_block_categories: list = field(default_factory=lambda: ["sponsor", "selfpromo", "interaction"])

    # ---- Stage 3 ----
    enable_stage3: bool = True
    min_video_duration_seconds: int = 120
    high_value_threshold: float = 20.0
    stage3_concurrency_limit: int = 1
    parallel_stage2_3: bool = True
    stage3_poll_interval: int = 30
    segment_chunk_size: int = 40000
    max_input_chars: int = 40000
    enable_segmented_summary: bool = True
    cognitive_value_threshold: float = 20.0
    dual_summary_threshold: float = 0.0        # 双摘要触发阈值 (0=沿用 cognitive_value_threshold)
    model_small: str = "deepseek-v4-flash"
    model_large: str = "deepseek-v4-pro"

    # ---- Stage 4 ----
    enable_stage4: bool = True
    top_percentile: float = 0.5
    cif_completion_rate_max_cap: float = 1.2
    cif_knowledge_score_weight: float = 0.1
    cif_base_behavior_weight: float = 2.0

    # ---- Stage 5 ----
    enable_stage5: bool = True
    stage5_model: str = "deepseek-v4-pro"      # 人物 SKILL 生成，不需要深度推理，v4-pro 更快

    # ---- Stage 2 (字幕提取) ----
    stage2_account_label: str = "主账号"      # 字幕提取使用的B站账号: "主账号"=credential.json, 其他=guest_credential.json

    # ---- UP Persona Pipeline (Stage UP) ----
    enable_up_persona: bool = False
    up_follower_threshold: int = 1000000        # 最少粉丝数才纳入
    up_view_threshold_factor: float = 0.07      # 播放量门槛系数: sqrt(粉丝数) * factor = 采纳视频数
    up_max_video_count: int = 1000              # 单个UP最多抓取视频数上限
    up_persona_dir: str = field(init=False)    # data/up_persona/
    up_skill_template_path: str = "references/nuwa-skill/skill-template.md"
    up_framework_path: str = "references/nuwa-skill/extraction-framework.md"
    up_stage2_3_parallel: bool = True          # UP视频的字幕提取和总结是否并行
    up_model: str = "deepseek-v4-pro"          # UP Skill 生成模型
    up_concurrency: int = 2                   # 同时处理多少个 UP (1=串行)

    # ---- Whisper 本地兜底 (Stage 2) ----
    enable_local_whisper: bool = True      # 字幕/总结都拿不到时走本地语音转文字
    whisper_model_size: str = "large-v3"     # tiny(1G)/small(2.4G)/medium(6G)/large-v3; 多语混用选large-v3
    whisper_device: str = "cuda"           # cuda / cpu
    whisper_compute_type: str = "float16"  # float16 / int8_float16 (低显存) / int8
    whisper_language: str = "zh"           # ""=auto "zh"=强制中文; 中外混剪纪录片开头可能是外语，auto会误判

    # ---- Misc ----

    def __post_init__(self):
        d = self.data_dir
        self.account_dir = os.path.join(d, "account")
        self.stage1_dir = os.path.join(d, "stage1_collector")
        self.enrich_dir = os.path.join(d, "stage1_enrich")
        self.stage2_dir = os.path.join(d, "stage2_subtitles")
        self.subtitles_dir = os.path.join(self.stage2_dir, "parsed_videos")
        self.stage3_dir = os.path.join(d, "stage3_summaries")
        self.persona_dir = os.path.join(d, "stage4_persona_builder")
        self.research_dir = os.path.join(self.persona_dir, "references", "research")
        self.history_dir = os.path.join(self.persona_dir, "history")
        self.system_dir = os.path.join(d, "system")
        self.log_dir = os.path.join(self.system_dir, "logs")
        self.credential_path = os.path.join(self.account_dir, "credential.json")
        self.guest_credential_path = os.path.join(self.account_dir, "guest_credential.json")
        self.deepseek_config_path = os.path.join(self.account_dir, "deepseek_config.json")
        self.master_index_file = os.path.join(self.stage1_dir, "master_index.json")
        self.master_enriched_file = os.path.join(self.enrich_dir, "master_enriched.json")
        self.subtitle_progress_file = os.path.join(self.stage2_dir, "stage2_progress.json")
        self.stage3_progress_file = os.path.join(self.stage3_dir, "stage3_progress.json")
        self.enrich_progress_file = os.path.join(self.enrich_dir, "enrich_progress.json")
        self.skill_output_path = os.path.join(self.persona_dir, "SKILL.md")
        self.template_path = os.path.join("references", "skill-template.md")
        self.framework_path = os.path.join("references", "extraction-framework.md")
        self.cognitive_value_threshold = self.high_value_threshold
        self.up_persona_dir = os.path.join(d, "up_persona")

        # ensure directories
        for p in [self.account_dir, self.stage1_dir, self.enrich_dir,
                   self.stage2_dir, self.subtitles_dir, self.stage3_dir,
                   self.persona_dir, self.research_dir, self.history_dir,
                   self.system_dir, self.log_dir, self.up_persona_dir]:
            os.makedirs(p, exist_ok=True)
