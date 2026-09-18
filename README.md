# video-editor

智能剪辑通用视频文件（MP4、MOV、MKV 等）：自动检测语音停顿、气声与死寂空白，结合大模型（默认 Qwen 3.8 Omni Flash）并发滑窗识别口误重讲、即时字词打架、假起步和废案，通过 ffmpeg 硬件加速进行高保真无缝切片导出，并支持切口微淡化消除爆音与导出审计报告。

[快速开始](#快速开始) · [核心用法](#核心用法) · [数据边界](#数据边界)

---

## 有什么用

视频创作者在录制口播、教程、会议或演讲时，粗剪往往耗费大量人工：
- **不使用时**：需要手动逐个排查并删除几百处几百毫秒的死寂气口；人工拉片识别重讲口误容易漏删或手抖吞字；跳切边缘缺少微淡化容易产生刺耳的“咔哒”爆音。
- **使用后**：全自动识别并剔除无声断点，利用字级 ASR 施加文字保护垫严防切字；滑窗推理自动提炼重复废案与假起步；切点吸附至波形极小值并应用 15ms 微淡化，一键生成剪辑成片与审计报告。

> **注意**：本工具用于**现成独立视频文件**（MP4/MOV/MKV）。Screen Studio 工程请使用 `screen-studio-editor`；成片字幕生成请使用 `oil-subtitle`。

---

## 快速开始

运行环境：macOS / Linux、Python 3.10+、ffmpeg / ffprobe。

```bash
git clone https://github.com/oil-oil/video-editor ~/.claude/skills/video-editor
```

或通过 skills CLI 安装：
```bash
npx skills add oil-oil/video-editor
```

系统需安装有 `ffmpeg` 与 `bl` CLI（或配置 `DASHSCOPE_API_KEY` 环境变量）。

---

## 核心用法

### 1. 全自动智能粗剪

```bash
python3 scripts/video_editor.py cut /path/to/video.mp4 -o /path/to/video_edited.mp4
```

- 默认使用 macOS 原生 `h264_videotoolbox` 硬件加速。
- 自动提取音频校准底噪、FunAudio ASR 字级转录、自适应停顿切除、Qwen 滑窗口误识别、波形极值吸附与 15ms 微淡化组装。

### 2. 预览分析与审计报告（Dry-run）

若先不出片，只查看删减规划与口误明细：

```bash
python3 scripts/video_editor.py cut /path/to/video.mp4 --dry-run
```

在同目录 `.<name>_work/<name>_edit_report.md` 生成详细表格：包含时间戳、删减内容、替换内容与删减理由。

### 3. 极速流拷贝模式（免重编码）

```bash
python3 scripts/video_editor.py cut /path/to/video.mp4 --fast-copy -o /path/to/video_fast.mp4
```

按关键帧无损切割流拷贝，耗时仅数秒。

---

## 配置与数据边界

配置保存于 `~/.config/video-editor/config.json`（可选）：

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

- **数据隐私**：音视频画面**完全在本地处理**，仅提取出的 16kHz 语音音轨经百炼 ASR 转录及文本片段送入大模型，视频帧绝不上传云端。
- **密钥安全**：密钥从 `DASHSCOPE_API_KEY` 或 `~/.bailian/config.json` 读取，严禁硬编码至代码仓库。

---

## 测试

运行内置单元测试集：

```bash
python3 -m unittest discover -s tests -p "test_*.py"
```
