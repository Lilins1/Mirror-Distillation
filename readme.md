# Mirror 蒸馏 (Mirror Distillation)

> "You are what you consume."  
> 信息摄入即认知镜像。

Mirror 蒸馏是一个面向个人的认知数字分身（Digital Twin）构建管线。它不做传统意义上的"聊天风格模仿"或"输出端行为克隆"，而是从用户在 Bilibili 上的深度信息消费行为出发，反向提炼更稳定的认知结构，包括：

- 用户长期高频认同的核心知识域
- 可迁移到新问题上的心智模型
- 默认调用的决策启发式
- 表达 DNA、认知边界与内在张力

项目的目标不是生成一个"像用户说话"的 AI，而是重构一个更接近"用户如何思考"的认知镜像系统。

## 项目定位

Mirror 蒸馏试图回答的问题是：

- 用户反复看完、点赞、投币、收藏的内容，究竟在说明什么？
- 这些内容能否被提炼成跨问题复用的判断结构？
- 一个 AI 是否能基于这些结构，成为用户的"认知镜面"而不是"聊天替身"？

项目强调三点：

- **输入端建模**：从"看了什么、认同什么、沉浸什么"入手，而不是从"说了什么"入手。
- **结构化蒸馏**：不是堆砌兴趣标签，而是提取可复现、可迁移、可解释的底层模型。
- **诚实边界**：承认数据局限，不把单个视频作者观点、短期热点或模板示例误写成用户长期人格。

## 环境配置

### 1. Conda 环境

推荐使用项目已配置的 `mirror_distill` conda 环境：

```powershell
conda activate mirror_distill
```

Python 版本：`>= 3.10`

### 2. 依赖安装

```powershell
pip install httpx qrcode openai bilibili-api-python
```

核心依赖说明：

| 包 | 用途 |
|---|---|
| `httpx` | 异步 HTTP 客户端，所有 B站 API 调用 |
| `openai` | DeepSeek LLM 调用（兼容 OpenAI SDK） |
| `bilibili-api-python` | Stage 2 字幕提取（官方库） |
| `qrcode` | 终端二维码登录 |

### 3. Bilibili 凭证

首次运行时，脚本会触发二维码登录，生成两个凭证文件：

- `data/account/credential.json` — 主账号（历史记录采集 + UP 视频抓取）
- `data/account/guest_credential.json` — 小号/访客凭证（字幕提取，减少主号风控）

也可以手动运行登录脚本刷新凭证。

### 4. DeepSeek 配置

创建 `data/account/deepseek_config.json`：

```json
{
  "api_key": "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "base_url": "https://api.deepseek.com/v1"
}
```

- `api_key` — 必填
- `base_url` — 可省略，默认 `https://api.deepseek.com/v1`

## 项目架构

```
Bilili_mirror_distill/
├── scripts/                          # 核心代码包
│   ├── config.py                     # 统一配置中心 (PipelineConfig dataclass)
│   ├── pipeline.py                   # 管线总控台 (PipelineRunner)
│   ├── auth.py                       # B站 认证 (二维码登录 + Cookie 管理)
│   ├── wbi.py                        # Wbi 签名模块 (所有需签名的 API 共用)
│   ├── storage.py                    # 数据 I/O 工具
│   ├── collector.py                  # Stage 1: 历史观看记录采集
│   ├── enricher.py                   # Stage 1.5: CIF 提纯与互动赋权
│   ├── extractor.py                  # Stage 2: 字幕 / AI 总结提取
│   ├── summarizer.py                 # Stage 3: LLM 深度蒸馏
│   ├── aggregator.py                 # Stage 4: 研究报告生成
│   ├── skill_generator.py            # Stage 5: SKILL.md 生成
│   ├── research_merger.py            # Stage 5: 研究合并
│   ├── quality_checker.py            # Stage 5: 质量检查
│   ├── guard.py                      # 定时守护器 (日常/周报双轨)
│   ├── up_persona.py                 # UP Persona 管线 (关注 UP 主认知建模)
│   └── test_up_api.py               # UP 相关 API 测试脚本
├── references/                       # 方法论文档与模板
│   ├── extraction-framework.md       # 认知提取框架 (Stage 4-5 用)
│   ├── skill-template.md             # SKILL.md 输出模板 (Stage 5 用)
│   └── nuwa-skill/                   # UP Persona 专用模板
│       ├── extraction-framework.md
│       └── skill-template.md
├── data/                             # 数据目录 (运行时生成)
│   ├── account/                      # 凭证 & DeepSeek 配置
│   ├── stage1_collector/             # 历史采集索引
│   ├── stage1_enrich/                # CIF 提纯结果
│   ├── stage2_subtitles/             # 字幕/官方总结
│   ├── stage3_summaries/             # LLM 知识蒸馏颗粒
│   ├── stage4_persona_builder/       # 研究报告 + 最终 SKILL.md
│   ├── system/                       # 守护器状态 & 日志
│   └── up_persona/                   # UP Persona 输出 ({领域}/{用户名}/)
├── pipeline_runner.py                # 入口脚本 (向后兼容)
└── readme.md
```

## 管线阶段

### Stage 1 — 历史采集 (`collector.py`)

- 登录 Bilibili，增量拉取历史观看记录
- 建立全局视频索引账本
- 支持断点衔接与深度重扫
- 输出: `data/stage1_collector/master_index.json`

### Stage 1.5 — CIF 提纯 (`enricher.py`)

- 补充视频分类、简介、标签
- 查询点赞、投币、收藏、关注等互动关系
- 重新计算 Cognitive Impact Factor (CIF)，将"看过"升级为"认知重要度排序"
- 输出: `data/stage1_enrich/master_enriched.json`

### Stage 2 — 字幕提取 (`extractor.py`)

- 优先获取 B站官方 AI 总结
- 若无则尝试获取官方字幕
- SponsorBlock 广告过滤
- 输出: `data/stage2_subtitles/parsed_videos/*.json`

### Stage 3 — LLM 蒸馏 (`summarizer.py`)

- 读取 Stage 2 文本，按 CIF 和长度动态选择模型
- 生成结构化知识摘要，提取认知画像字段
- 支持长文本分段、高价值节点优先大模型
- 输出: `data/stage3_summaries/*.json`

### Stage 4 — 研究聚合 (`aggregator.py`)

- 读取 Stage 3 知识颗粒，重新计算高级 CIF
- 输出 6 维度研究报告:
  - `01-core-consumption.md` — 核心知识摄入域
  - `02-value-resonances.md` — 价值共鸣点
  - `03-expression-dna.md` — 表达风格映射
  - `04-boundaries-rejections.md` — 边界推断证据
  - `05-decision-heuristics.md` — 决策启发式
  - `06-timeline.md` — 时间切片/演化观察

### Stage 5 — Skill 生成 (`skill_generator.py` + 辅助)

- 基于研究报告 + 方法论文档 + 模板，调用 DeepSeek 直渲染 `SKILL.md`
- 自动执行质量检查（心智模型数量、边界标注、表达 DNA 完整度等）
- 最终产物: `data/stage4_persona_builder/SKILL.md`

### Stage UP — UP 主人物 Skill (`up_persona.py`)

- 从关注列表中筛选高粉 UP 主（默认 ≥100 万粉丝）
- 抓取 UP 的高播放视频（Wbi 签名，播放量降序）
- 按 B站分区名自动归类领域（科技/知识/游戏/生活/...）
- 复用 Stage 2 + Stage 3 提取字幕和 LLM 总结
- 按领域聚合研究报告，生成 UP 主第一人称 `SKILL.md`
- 输出: `data/up_persona/{领域}/{用户名}/SKILL.md`

## 使用方法

### 运行完整管线

```powershell
conda activate mirror_distill
python -m scripts.pipeline --stage all
```

### 按阶段单独执行

```powershell
python -m scripts.pipeline --stage 1     # 仅历史采集
python -m scripts.pipeline --stage 1.5   # 仅 CIF 提纯
python -m scripts.pipeline --stage 2     # 仅字幕提取
python -m scripts.pipeline --stage 3     # 仅 LLM 蒸馏
python -m scripts.pipeline --stage 4     # 仅研究聚合
python -m scripts.pipeline --stage 5     # 仅 Skill 生成
python -m scripts.pipeline --stage up    # 仅 UP Persona
```

### 日常 / 周报模式

```powershell
python -m scripts.pipeline --stage daily   # Stage 1-3
python -m scripts.pipeline --stage all     # Stage 1-5 + UP
```

### 调试模式

```powershell
python -m scripts.pipeline --stage all --debug
```

调试模式会限制抓取页数和处理样本量，适合验证流程是否通畅。

### UP Persona 独立运行

```powershell
# 处理所有关注的高粉 UP
python -m scripts.up_persona

# 指定单个 UP 测试全流程
python -m scripts.up_persona --uid 946974 --max-videos 20

# 调试模式 + 自定义粉丝阈值
python -m scripts.up_persona --debug --threshold 500000 --limit 5
```

参数说明：

| 参数 | 说明 |
|---|---|
| `--uid` | 指定 UP mid，跳过关注列表直接测试 |
| `--max-videos` | 每个 UP 最多抓取视频数 (0=自动计算) |
| `--threshold` | 粉丝数阈值 (覆盖 config) |
| `--limit` | 仅处理前 N 个 UP |
| `--debug` | 调试模式 |

### API 测试

```powershell
python -m scripts.test_up_api              # 完整测试
python -m scripts.test_up_api --uid 946974 # 测试指定 UP
```

### 定时守护

```powershell
# 默认: 每24h触发日常, 每168h触发周报
python -m scripts.guard

# 自定义周期
python -m scripts.guard --daily-hours 12 --weekly-hours 72

# 强制执行全管线
python -m scripts.guard --force
```

## 配置参考

所有配置集中在 [scripts/config.py](scripts/config.py) 的 `PipelineConfig` dataclass 中：

### 路径

| 字段 | 默认值 | 说明 |
|---|---|---|
| `data_dir` | `"data"` | 数据根目录 |

### Stage 1

| 字段 | 默认值 | 说明 |
|---|---|---|
| `prod_max_pages` | `200` | 生产模式最大翻页数 |
| `deep_scan_interval` | `259200` (3天) | 深度重扫间隔 (秒) |

### 并发

| 字段 | 默认值 | 说明 |
|---|---|---|
| `concurrency_limit` | `1` | Stage 1.5 并发数 |

### Stage 2

| 字段 | 默认值 | 说明 |
|---|---|---|
| `enable_sponsor_block` | `True` | 启用 SponsorBlock 广告过滤 |
| `sponsor_block_categories` | `["sponsor", "selfpromo", "interaction"]` | 过滤的广告类别 |

### Stage 3

| 字段 | 默认值 | 说明 |
|---|---|---|
| `enable_stage3` | `True` | 启用 LLM 蒸馏 |
| `min_video_duration_seconds` | `120` | 过滤短视频 (秒) |
| `high_value_threshold` | `20.0` | 高价值节点 CIF 阈值 |
| `parallel_stage2_3` | `True` | Stage 2/3 并行 |
| `enable_segmented_summary` | `True` | 长文本分段总结 |
| `model_small` | `"deepseek-v4-flash"` | 普通节点模型 |
| `model_large` | `"deepseek-v4-pro"` | 高价值节点模型 |

### Stage 4

| 字段 | 默认值 | 说明 |
|---|---|---|
| `enable_stage4` | `True` | 启用研究聚合 |
| `top_percentile` | `0.5` | 进入报告的高价值节点比例 |

### Stage 5

| 字段 | 默认值 | 说明 |
|---|---|---|
| `enable_stage5` | `True` | 启用 Skill 生成 |
| `stage5_model` | `"deepseek-reasoner"` | Skill 生成模型 |

### UP Persona

| 字段 | 默认值 | 说明 |
|---|---|---|
| `enable_up_persona` | `True` | 启用 UP Persona 管线 |
| `up_follower_threshold` | `1000000` | 最少粉丝数 |
| `up_view_threshold_factor` | `0.07` | 播放量门槛系数 |
| `up_max_video_count` | `1000` | 单个 UP 最多抓取视频数 |
| `up_model` | `"deepseek-v4-pro"` | Skill 生成模型 |

### 调试

| 字段 | 默认值 | 说明 |
|---|---|---|
| `debug_mode` | `False` | 调试模式 |
| `debug_item_limit` | `10` | 调试模式处理量上限 |
| `debug_max_pages` | `2` | 调试模式翻页上限 |

## 数据流

```
Stage 1 (collector)      → master_index.json
Stage 1.5 (enricher)     → master_enriched.json
Stage 2 (extractor)      → parsed_videos/*.json
Stage 3 (summarizer)     → stage3_summaries/*.json
Stage 4 (aggregator)     → research/*.md (6 维度报告)
Stage 5 (skill_generator) → SKILL.md (最终认知镜像)
Stage UP (up_persona)    → up_persona/{领域}/{用户名}/SKILL.md
```

## 输出结构

### 个人认知镜像

```
data/stage4_persona_builder/
├── SKILL.md                          # 最终可注入 LLM 的认知镜像
└── references/research/
    ├── 01-core-consumption.md        # 核心知识摄入域
    ├── 02-value-resonances.md        # 价值共鸣点
    ├── 03-expression-dna.md          # 表达风格映射
    ├── 04-boundaries-rejections.md   # 边界推断证据
    ├── 05-decision-heuristics.md     # 决策启发式
    └── 06-timeline.md                # 时间切片
```

### UP 主人物

```
data/up_persona/
├── 科技/
│   ├── 影视飓风/
│   │   ├── summaries/                # LLM 蒸馏颗粒
│   │   ├── research/                 # 研究报告
│   │   └── SKILL.md                  # 人物认知镜像
│   └── 极客湾/
│       └── ...
├── 知识/
│   └── ...
└── 游戏/
    └── ...
```

## 常见问题

### 1. Wbi 签名 412 错误

可能原因和排查：

1. **凭证过期** — 重新运行登录刷新 `credential.json`
2. **缺少 Referer** — `up_persona.py` 已添加 `Referer: https://space.bilibili.com/`，确保未删除
3. **wbi mixin 表过时** — 参见 [scripts/wbi.py](scripts/wbi.py)，已与官方库对齐
4. **请求频率过高** — 代码已有指数退避和深睡眠机制，可适当增加 `asyncio.sleep` 间隔

### 2. UP Persona 产出 0 个有效总结

检查以下几点：

- B站 space API 返回的 `length` 字段是否为字符串格式 (`"MM:SS"`) — `_parse_length()` 已处理
- `min_video_duration_seconds` 阈值是否过高（默认 120s）
- API 是否返回空的 vlist（可能是凭证问题，尝试用 `test_up_api.py` 诊断）

### 3. 为什么生成的 Skill 像"高智感文案"而不是认知镜像？

- Stage 4 高价值节点比例过高 (`top_percentile`)，信号被稀释
- 时间证据不足却被写成"认知演化"
- 模板或提示词示例过强，模型开始套模板

建议优先检查 `top_percentile`、方法论文档和 Stage 4 研究报告。

### 4. SponsorBlock 没起作用

不一定代码有问题。可能原因：对应视频没有社区标注、网络环境导致请求失败、广告类别不在过滤列表中。先查看 Stage 2 输出中的 `sponsor_block` 状态字段。

### 5. 心智模型数量不足 (质量检查)

通常说明证据约束变严格后只筛出了少数真正合格的模型。这不一定是坏事 — 往往说明系统开始少编造、少补写了。

### 6. 如何单独蒸馏某个 UP 主？

```powershell
python -m scripts.up_persona --uid <UP的mid> --max-videos 50
```

例如蒸馏影视飓风 (mid=946974) 的前 50 个高播放视频：

```powershell
python -m scripts.up_persona --uid 946974 --max-videos 50
```

## 方法论文档

### `references/extraction-framework.md`

定义了心智模型识别标准、决策启发式提取标准、表达 DNA 提取标准、边界与张力处理规则、时间证据门槛、反诱导与反偏置约束。

### `references/skill-template.md`

定义了最终 Skill 输出模板的文档结构、各章节必须包含的字段、对证据锚点/跨域复现/局限性的硬要求。

### `references/nuwa-skill/`

UP Persona 管线的专用方法论文档和模板，结构类似但针对单个 UP 主的人物建模做了适配。

## 当前实现状态

已具备：

- 完整的数据采集、提纯、蒸馏、研究、生成闭环
- 基于方法论文档和模板的可控生成
- 自动化质量检查
- UP 主认知建模管线

仍值得继续优化的方向：

- 更稳定的高价值节点筛选策略
- 更强的时间线数据密度
- 更细粒度的边界反推
- SponsorBlock 命中质量排查
- 最终 Skill 结果的自动回归评估
- Wbi 签名的自动更新机制
