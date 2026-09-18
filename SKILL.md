---
name: video-editor
description: "智能剪辑通用视频文件（MP4、MOV、MKV 等）：自动检测语音停顿、气声与死寂空白，结合大模型（如 Qwen 3.8 Omni Flash）并发滑窗识别口误重讲、即时字词打架、假起步和废案，通过 ffmpeg 硬件加速进行高保真无缝切片导出，并支持切口微淡化消除爆音与导出审计报告。用户提供现成视频文件、要求自动粗剪口误死寂停顿、压缩讲解节奏或导出剪辑时间线时使用。不用于 Screen Studio 工程剪辑（Screen Studio 工程请使用 screen-studio-editor），不负责字幕生成（字幕请使用 oil-subtitle）。"
---

# Video Editor

智能粗剪通用视频文件（MP4、MOV、MKV 等）。无需 Screen Studio 工程文件，直接基于现成视频文件完成“气口/死寂停顿切除 + 语义口误与废案删减 + 接缝平滑微淡化 + 硬件加速重构导出”。

后续字幕生成交给 `oil-subtitle`。

---

## 目标

为口播、教程、会议录像、vlog 等各类视频创作者解决剪辑中最繁琐的“粗剪阶段”：
1. **彻底消除死寂停顿**：自适应能量底噪与 VAD 双重检测，标点边界敏感识别（句末 350ms，词间 450ms），并留出舒适气口（180ms），绝不吞字。
2. **精准切除口误与废案**：基于阿里百炼 FunAudio ASR 字级转录，通过逗号/句号微粒原子化切分与滑窗并发 LLM 推理（默认 Qwen 3.8 Omni Flash），准确召回“假起步”、“说错重讲”、“字词打架”等口误。
3. **音画无缝衔接**：切点吸附至局部波形振幅极小值，拼接处施加 15ms 音频微淡化（crossfade/afade），彻底杜绝跳切时的“爆音”、“咔哒”杂音。
4. **一键无损或极速导出**：macOS 原生 VideoToolbox 硬件加速导出；同时支持免重编码的流拷贝模式（`--fast-copy`）。

---

## 环境与配置

### 1. 基础依赖

- `ffmpeg`、`ffprobe`：已安装在系统 PATH 或 `/opt/homebrew/bin/`。
- `bl` CLI（阿里百炼 CLI）或 DashScope API Key。
- Python 3.10+。

### 2. API Key 与模型配置

配置文件路径：`~/.config/video-editor/config.json`（可选，不存在时使用默认值）：

```json
{
  "model": "qwen3.8-omni-flash",
  "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "pause_threshold_ms": 450.0,
  "sentence_threshold_ms": 350.0,
  "min_pause_ms": 180.0,
  "crossfade_ms": 15.0,
  "concurrency": 5
}
```

- API Key 读取顺序：`DASHSCOPE_API_KEY` 环境变量 -> `~/.bailian/config.json` -> `~/.config/video-editor/config.json`。

---

## 工作流

### 1. 默认智能剪辑（推荐）

对源视频进行完整分析并导出剪辑后的新视频：

```bash
python3 scripts/video_editor.py cut /path/to/video.mp4 -o /path/to/video_edited.mp4
```

工作流程：
1. **音频提取与声学校准**：提取 16kHz WAV 音频，自适应计算能量分布分位数与底噪。
2. **高精度 ASR 字级识别**：调用百炼 FunAudio ASR 生成包含字级时间戳的转录文本。
3. **自适应停顿检测与文字保护**：结合 Silero VAD 与句尾标点感知切分死寂停顿，对识别出的文字施加保护垫，严防切字。
4. **全片语义口误滑窗规划**：按子句标点切分为原子片段，滑窗送入 Qwen 3.8 Omni Flash 识别口误废案。
5. **切点波形极小值吸附**：将删减区间微调至最近的局部静音/波形低谷点。
6. **硬件加速拼接与微淡化**：采用 `h264_videotoolbox` 快速重编码，每段保留切片首尾施加 15ms `afade`。
7. **生成 Markdown 审计报告**：在工作目录生成剪辑明细对照表。

---

### 2. 预览分析与试运行（Dry-run）

若只想查看哪些停顿和口误会被切除，而不实际渲染大视频：

```bash
python3 scripts/video_editor.py cut /path/to/video.mp4 --dry-run
```

- 运行后在临时工作目录 `.video_work/` 下生成 `video_edit_report.md`。
- 报告中包含：原始时长、预计时长、压缩比例、每一处口误删减区间、原话、订正保留话术与删减理由。

---

### 3. 极速流拷贝剪辑（Fast-copy）

适用于对速度要求极高且不需要重编码的场景（按关键帧直接分片串接）：

```bash
python3 scripts/video_editor.py cut /path/to/video.mp4 --fast-copy -o /path/to/video_fast.mp4
```

---

### 4. 离线复核与重新渲染（Review / Re-render）

基于已有的剪辑工作目录审查或调整参数重新渲染：

```bash
python3 scripts/video_editor.py review /path/to/video.mp4
```

---

## 输出规范

1. **剪辑后视频**：
   - 命名：默认在源文件同目录下生成 `<name>_edited.<ext>`，或由 `-o` 指定。
   - 编码：保持原画质尺寸与帧率，音频 192k AAC，首尾淡化平滑无爆音。
2. **审计报告**：
   - 保存在源文件旁的 `.<name>_work/<name>_edit_report.md`。
   - 提供 Markdown 表格，清晰记录各处删减的前后文与删减理由。

---

## 资源导航

- `scripts/video_editor.py`: 统一 CLI 入口工具。
- `scripts/audio_extractor.py`: 音频提取与声学能量分析。
- `scripts/transcriber.py`: 阿里百炼 ASR 转录模块（支持 `bl` CLI 与 SDK）。
- `scripts/silence_detector.py`: 自适应静音检测、VAD 与字保护算法。
- `scripts/semantic_planner.py`: 子句原子化切分与 Qwen 3.8 Omni Flash 滑窗语义规划。
- `scripts/video_assembler.py`: VideoToolbox 硬件切片组装、15ms 音频微淡化与报告生成。
- `tests/`: 完整单元测试集。
