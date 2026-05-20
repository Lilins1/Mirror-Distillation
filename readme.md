# Mirror 蒸馏 (Mirror Distillation)

> "You are what you consume."  
> 信息摄入即认知镜像。

Mirror 蒸馏是一个面向个人的认知数字分身（Digital Twin）构建管线。  
它不做传统意义上的“聊天风格模仿”或“输出端行为克隆”，而是从用户在 Bilibili 上的深度信息消费行为出发，反向提炼更稳定的认知结构，包括：

- 用户长期高频认同的核心知识域
- 可迁移到新问题上的心智模型
- 默认调用的决策启发式
- 表达 DNA、认知边界与内在张力

项目的目标不是生成一个“像用户说话”的 AI，而是重构一个更接近“用户如何思考”的认知镜像系统。

## 项目定位

Mirror 蒸馏试图回答的问题是：

- 用户反复看完、点赞、投币、收藏的内容，究竟在说明什么？
- 这些内容能否被提炼成跨问题复用的判断结构？
- 一个 AI 是否能基于这些结构，成为用户的“认知镜面”而不是“聊天替身”？

项目强调三点：

- **输入端建模**：从“看了什么、认同什么、沉浸什么”入手，而不是从“说了什么”入手。
- **结构化蒸馏**：不是堆砌兴趣标签，而是提取可复现、可迁移、可解释的底层模型。
- **诚实边界**：承认数据局限，不把单个视频作者观点、短期热点或模板示例误写成用户长期人格。

## 核心思路

Mirror 的核心不是“用户喜欢哪些视频”，而是：

1. 找出哪些内容被深度消费且具有高知识密度。
2. 判断这些内容背后是否存在跨领域复现的底层逻辑。
3. 把这些逻辑转化为“遇到陌生问题时会怎样拆解”的认知策略。
4. 进一步生成可注入大模型的 `SKILL.md`，让模型在交互中优先调用这套认知结构。

这意味着最终产物应更像：

- 一个可执行的认知操作系统

而不是：

- 一段“高智感人设文案”
- 一个简单的兴趣画像
- 某位 Up 主风格的拼贴模仿

## 管线架构

当前代码已经落地为 5 个阶段，外加若干中间检查与汇总脚本。

### Stage 1: 历史采集

对应脚本：

- `stage1_collector.py`

职责：

- 登录 Bilibili 账号
- 增量拉取历史观看记录
- 建立全局视频索引账本
- 为后续阶段保留基础元数据

输出：

- `data/stage1_collector/master_index.json`
- `data/stage1_collector/index_links_*.json`

特点：

- 支持二维码登录
- 支持断点衔接与深度重扫
- 自动构建基础 `view_at / progress / duration / author` 等字段

### Stage 1.5: CIF 提纯与互动赋权

对应脚本：

- `stage1_enrich_cif.py`

职责：

- 补充视频的分类、简介、标签
- 查询点赞、投币、收藏、关注等互动关系
- 重新计算 `Cognitive Impact Factor (CIF)`

输出：

- `data/stage1_enrich/master_enriched.json`
- `data/stage1_enrich/enriched_links_*.json`

特点：

- 支持断点续跑
- 安全写盘，减少中断损坏风险
- 把“看过”升级为“认知重要度排序”

### Stage 2: 字幕 / 官方总结提取

对应脚本：

- `stage2_subtitle_extractor.py`

职责：

- 优先获取 B 站官方 AI 总结
- 若无 AI 总结，则尝试获取官方字幕
- 对字幕内容执行 SponsorBlock 广告过滤

输出：

- `data/stage2_subtitles/parsed_videos/*.json`
- `data/stage2_subtitles/stage2_progress.json`

特点：

- 优先走“官方总结 / 官方字幕”路径，尽量避免重型本地转录
- 已接入 SponsorBlock 接口
- 当前 SponsorBlock 的命中率受社区标注覆盖和网络环境影响，若命中较少，不一定是代码错误

### Stage 3: LLM 深度蒸馏

对应脚本：

- `stage3_summarizer.py`

职责：

- 读取 Stage 2 的文本结果
- 按 CIF 和文本长度动态选择模型
- 生成结构化知识摘要
- 提取初步的认知画像字段

输出：

- `data/stage3_summaries/*.json`
- `data/stage3_summaries/stage3_progress.json`

特点：

- 支持长文本分段总结
- 支持高价值节点优先使用更强模型
- 提取字段包括：
  - `summary`
  - `tags`
  - `knowledge_value_score`
  - `cognitive_profile`
  - `cognitive_signal_strength`

### Stage 4: 研究报告变压

对应脚本：

- `stage4_aggregate_to_nuwa.py`

职责：

- 读取 Stage 3 的结构化知识颗粒
- 重新计算高级 CIF
- 输出 6 个维度的研究报告 Markdown

输出目录：

- `data/stage4_persona_builder/references/research/`

核心产物：

- `01-core-consumption.md`
- `02-value-resonances.md`
- `03-expression-dna.md`
- `04-boundaries-rejections.md`
- `05-decision-heuristics.md`
- `06-timeline.md`

说明：

- `04` 现在已经改为边界证据整理，不再直接用说明式模板诱导模型
- `06` 现在包含“时间证据充分性判断”，不足时会降级为“近期认知切片”

### Stage 5: 最终 Skill 生成

对应脚本：

- `stage5_generate_skill.py`
- `stage5_merge_research.py`
- `stage5_quality_check.py`

职责：

- 读取 Stage 4 研究报告
- 读取方法论文档与模板
- 调用 DeepSeek 直接生成最终 `SKILL.md`
- 自动执行质量检查

输出：

- `data/stage4_persona_builder/SKILL.md`

特点：

- 当前已改为**单次直渲染**
- 不再先抽中间 JSON 蓝图，减少中间态压缩导致的信息损耗
- 直接基于：
  - `references/extraction-framework.md`
  - `references/skill-template.md`
  - `data/stage4_persona_builder/references/research/*.md`
  一次性生成最终认知镜像

## 目录结构

项目主目录大致如下：

```text
Bilili_mirror_distill/
├─ stage1_collector.py
├─ stage1_enrich_cif.py
├─ stage2_subtitle_extractor.py
├─ stage3_summarizer.py
├─ stage4_aggregate_to_nuwa.py
├─ stage5_generate_skill.py
├─ stage5_merge_research.py
├─ stage5_quality_check.py
├─ pipeline_runner.py
├─ references/
│  ├─ extraction-framework.md
│  └─ skill-template.md
└─ data/
   ├─ account/
   ├─ stage1_collector/
   ├─ stage1_enrich/
   ├─ stage2_subtitles/
   ├─ stage3_summaries/
   └─ stage4_persona_builder/
```

## 环境要求

- Python `>= 3.10`
- Windows 环境下已在脚本中处理了 `UTF-8` 和事件循环兼容
- 需要可访问：
  - Bilibili
  - DeepSeek API
  - SponsorBlock API（若启用）

## 安装依赖

最小依赖可先安装：

```bash
pip install bilibili-api-python httpx qrcode openai
```

建议在虚拟环境中运行：

```bash
python -m venv .venv
.venv\Scripts\activate
pip install bilibili-api-python httpx qrcode openai
```

如果你需要自行测试环境，可参考：

- `test_env.py`

## 配置说明

### 1. Bilibili 登录凭证

首次运行 Stage 1 或 Stage 2 时，脚本会触发二维码登录。

输出目录：

- `data/account/credential.json`
- `data/account/guest_credential.json`

说明：

- 主账号用于历史记录采集与关系查询
- 小号 / Guest 凭证用于字幕和总结提取，减少权限污染

### 2. DeepSeek 配置

创建文件：

- `data/account/deepseek_config.json`

示例：

```json
{
  "api_key": "sk-xxxx",
  "base_url": "https://api.deepseek.com/v1"
}
```

说明：

- `api_key` 必填
- `base_url` 可省略，默认即为 DeepSeek 官方地址

## 快速开始

### 方式一：运行完整管线

```bash
python pipeline_runner.py --stage all
```

### 方式二：调试模式运行

```bash
python pipeline_runner.py --stage all --debug
```

调试模式会限制抓取页数和处理样本量，更适合验证流程是否通畅。

### 方式三：按阶段单独执行

```bash
python pipeline_runner.py --stage 1
python pipeline_runner.py --stage 1.5
python pipeline_runner.py --stage 2
python pipeline_runner.py --stage 3
python pipeline_runner.py --stage 4
python pipeline_runner.py --stage 5
```

## pipeline_runner 说明

总控脚本：

- `pipeline_runner.py`

当前支持阶段：

- `all`
- `1`
- `1.5`
- `2`
- `3`
- `4`
- `5`

已支持的能力：

- Stage 2 与 Stage 3 并行运行
- Stage 4 自动汇总 Stage 3 数据
- Stage 5 自动执行：
  - 研究汇总
  - Skill 生成
  - 质量检查

## 关键配置项

`pipeline_runner.py` 中维护了统一配置中心：

- `DEBUG_MODE`
- `DEBUG_ITEM_LIMIT`
- `DEBUG_MAX_PAGES`
- `PROD_MAX_PAGES`
- `CONCURRENCY_LIMIT`
- `ENABLE_SPONSOR_BLOCK`
- `MIN_VIDEO_DURATION_SECONDS`
- `DEEPSEEK_HIGH_VALUE_THRESHOLD`
- `STAGE3_CONCURRENCY_LIMIT`
- `PARALLEL_STAGE2_3`
- `STAGE3_POLL_INTERVAL`
- `TOP_PERCENTILE`

其中尤其值得关注：

- `TOP_PERCENTILE`
  - 控制 Stage 4 进入最终研究报告的高价值节点比例
  - 比例过高会稀释“高价值信号”
  - 比例过低会导致证据过少
- `MIN_VIDEO_DURATION_SECONDS`
  - 过滤过短视频
- `PARALLEL_STAGE2_3`
  - 开启后可边提取边总结

## 数据流说明

从输入到输出，当前主路径如下：

1. `stage1_collector.py`
   - 输出历史索引
2. `stage1_enrich_cif.py`
   - 输出提纯后的高价值节点
3. `stage2_subtitle_extractor.py`
   - 输出字幕或官方总结文本
4. `stage3_summarizer.py`
   - 输出结构化知识颗粒
5. `stage4_aggregate_to_nuwa.py`
   - 输出 6 维研究报告
6. `stage5_generate_skill.py`
   - 输出最终 `SKILL.md`

## 输出结果说明

### 中间研究产物

目录：

- `data/stage4_persona_builder/references/research/`

它们用于给最终大模型生成阶段提供证据：

- `01-core-consumption.md`：核心知识摄入域
- `02-value-resonances.md`：价值共鸣点
- `03-expression-dna.md`：表达风格映射
- `04-boundaries-rejections.md`：边界推断证据
- `05-decision-heuristics.md`：决策启发式证据
- `06-timeline.md`：时间切片 / 演化观察

### 最终产物

文件：

- `data/stage4_persona_builder/SKILL.md`

这是最终可注入 LLM 的认知镜像配置文件。

## 方法论文档与模板

### `references/extraction-framework.md`

这是当前项目的核心方法论文档，定义了：

- 心智模型识别标准
- 决策启发式提取标准
- 表达 DNA 提取标准
- 边界与张力处理规则
- 时间证据门槛
- 反诱导与反偏置约束

### `references/skill-template.md`

这是最终 Skill 输出模板，定义了：

- 最终文档结构
- 各章节必须包含的字段
- 对证据锚点、跨域复现、局限性等内容的硬要求

## 质量检查

脚本：

- `stage5_quality_check.py`

当前检查项包括：

- 心智模型数量
- 模型局限性标注
- 表达 DNA 特征完整度
- 诚实边界条目数量
- 内在张力是否存在

示例：

```bash
python stage5_quality_check.py
```

如果你只想检查某个结果文件：

```bash
python stage5_quality_check.py data/stage4_persona_builder/SKILL.md
```

## 常见问题

### 1. 为什么生成出来的 Skill 很像“高智感文案”，不像认知镜像？

常见原因：

- Stage 4 高价值节点比例过高，信号被稀释
- 时间证据不足却被写成“认知演化”
- 模板或提示词示例过强，模型开始套模板
- 单个作者观点没有和用户长期模型剥离

建议优先检查：

- `TOP_PERCENTILE`
- `references/extraction-framework.md`
- `references/skill-template.md`
- `data/stage4_persona_builder/references/research/04-boundaries-rejections.md`
- `data/stage4_persona_builder/references/research/06-timeline.md`

### 2. SponsorBlock 没起作用，是代码坏了吗？

不一定。

可能原因：

- 对应视频没有社区标注
- 当前网络环境导致请求失败
- 该视频的广告类别不在当前过滤列表中

建议先查看 Stage 2 输出中的 `sponsor_block` 状态字段，而不是直接下结论。

### 3. `view_at` 时间戳是否需要额外转换？

通常不需要额外手动处理。

当前流程里：

- Stage 1 保存原始 Unix 时间戳
- Stage 4 已经把时间转成自然语言时间片
- Stage 5 读取的是研究报告，而不是直接读取原始 Unix 时间戳

### 4. 为什么质量检查提示“心智模型数量不足”？

这通常说明：

- 证据约束变严格后，只筛出了少数真正合格的模型
- 当前样本的跨域复现度不够
- Stage 4 研究报告对模型生成的支撑仍偏弱

这不一定是坏事，往往说明系统开始少编造、少补写了。

## 当前实现状态

当前仓库更适合作为实验性研究管线，而非完全打磨好的生产级产品。

已经具备：

- 完整的数据采集、提纯、蒸馏、研究、生成闭环
- 基于方法论文档和模板的可控生成
- 自动化质量检查

仍值得继续优化的方向：

- 更稳定的高价值节点筛选策略
- 更强的时间线数据密度
- 更细粒度的边界反推
- SponsorBlock 命中质量排查
- 最终 Skill 结果的自动回归评估

