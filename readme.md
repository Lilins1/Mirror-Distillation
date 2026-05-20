# Mirror 蒸馏 (Mirror Distillation) 🪞

> "You are what you consume." —— 信息摄入即认知镜像。

Mirror 蒸馏是一个开源的个人认知数字分身（Digital Twin）构建管线。本项目摒弃了传统的“输出端行为克隆”，创造性地通过反向工程，从用户在核心内容平台（Bilibili）的深度信息消费记录（观看、点赞、投币、收藏）中，提炼出底层的决策启发式与心智模型，最终重构出一个具备极高仿生度的专属 AI 思考者。

## 🚀 核心架构与功能

系统采用三阶段管线架构，兼顾数据隐私、反爬风控与大模型推理效率：

* **Stage 1: 多维数据采集与认知赋权** * 智能提权与二维码本地 Auth 登录。
    * 静默爬取历史记录，建立视频链接的全局拓扑图。
    * 动态计算认知影响因子 (Cognitive Impact Factor, CIF)。
* **Stage 2: 多模态提取与广告清洗 (Subtitle-First)**
    * 优先白嫖官方 AI 总结与 CC 软字幕。
    * 集成 SponsorBlock，精准过滤视频内嵌广告与废话。(目前没用，需要检查下是没人标记还是接口调用问题)
    * 降级策略：支持本地 Faster-Whisper 音频转写，保护隐私。
* **Stage 3: 认知推理与 AI 深度蒸馏**
    * 按 CIF 权重调用 DeepSeek (V4-Flash/Pro) 动态提纯。
    * 支持超长视频的分段递归总结，避免上下文衰减。
    * **核心亮点：** 从消费数据中逆推用户的表达 DNA、价值观底线与底层信念。

## 📦 安装与配置

**1. 环境依赖**
请确保 Python 版本 >= 3.10。
```bash
pip install bilibili-api-python httpx qrcode openai