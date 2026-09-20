<p align="center">
  <img src="assets/readme/hero.svg" alt="video-editor - 智能通用视频粗剪与口误废案剔除" width="100%"/>
</p>

# video-editor

智能剪辑通用视频文件（MP4、MOV、MKV 等）：自动检测语音停顿、气声与死寂空白，结合大模型（默认 Qwen 3.8 Omni Flash）并发滑窗识别口误重讲、即时字词打架、假起步和废案，通过 ffmpeg 硬件加速进行高保真无缝切片导出，并支持切口微淡化消除爆音与导出审计报告。

[快速开始](#快速开始) · [核心用法](#核心用法) · [实测准确度](#实测准确度) · [数据边界](#数据边界)

---

## 有什么用

视频创作者在录制口播、教程、会议或演讲时，粗剪往往耗费大量人工：
- **不使用时**：需要手动逐个排查并删除几百处几百毫秒的死寂气口；人工拉片识别重讲口误容易漏删或手抖吞字；跳切边缘缺少微淡化容易产生刺耳的“咔哒”爆音。
- **使用后**：全自动识别并剔除无声断点，利用字级 ASR 施加 60ms 文字保护垫严防切字；滑窗推理自动提炼重复废案与假起步；切点吸附至波形极小值并应用 15ms 微淡化，一键生成剪辑成片与审计报告。

> **边界说明**：本工具用于**现成通用视频文件**（MP4 / MOV / MKV）。若需处理 Screen Studio 原生工程，请使用 `screen-studio-editor`；成片字幕生成与烧录请使用 `oil-subtitle`。

---

## 快速开始

### 1. 安装 Skill

**方式 A：让 Agent 安装（推荐）**
向你的 Agent 发送：
```text
请帮我安装这个 Skill：https://github.com/oil-oil/video-editor
```

**方式 B：通过 skills CLI 安装**
```bash
npx skills add oil-oil/video-editor
```

### 2. 环境与依赖

- 系统环境：macOS（原生支持 VideoToolbox 硬件加速）或 Linux
- 软件依赖：Python 3.10+、`ffmpeg`、`ffprobe`
- 首次使用安装 Python 虚拟环境与依赖：
  ```bash
  bash setup.sh
  ```

### 3. API Key 配置

本工具需要使用阿里云百炼 DashScope API（用于 FunAudio 字级语音识别与 Qwen 3.8 Omni Flash 语义口误消除）。

**使用随附安全配置页（推荐，零明文暴露）：**
```bash
node scripts/credential-ui/src/profile.ts status default
node scripts/credential-ui/src/profile.ts setup default
```
终端会输出本机安全链接，由你亲自打开网页填写。凭据保存在系统的安全钥匙串（macOS Keychain / Windows Credential Manager / Linux Secret Service）中。

亦可直接配置环境变量：
```bash
export DASHSCOPE_API_KEY="your-api-key"
```

---

## 核心用法

### 1. 全自动智能粗剪（推荐）

对源视频进行完整分析、停顿与口误切除并导出剪辑后的新视频：

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

适用于对速度要求极高且不需要重编码的场景（按关键帧直接分片串接）：

```bash
python3 scripts/video_editor.py cut /path/to/video.mp4 --fast-copy -o /path/to/video_fast.mp4
```

---

## 实测准确度

在 5 个真实历史视频项目（涵盖短片口播、深度实操与长篇教程，累计 53.3 分钟原始视频）上，与专业创作者人工精剪定版数据（Human Ground Truth）进行毫秒级重合比对：

| 评测视频工程 | 原始时长 | 人工切除总长 | 算法切除总长 | 重合时长 | 精确率 (Precision) | 召回率 (Recall) | 综合得分 (F1) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **09-17 短片口播** | 03:50 (230s) | 74.1s | 70.3s | **65.7s** | **93.48%** 🎯 | **88.59%** 🚀 | **90.97%** |
| **09-13 短片口播** | 07:30 (450s) | 243.1s | 225.4s | **211.9s** | **94.00%** 🎯 | **87.16%** 🚀 | **90.45%** |
| **09-14 演示实录** | 07:11 (431s) | 207.4s | 174.1s | **160.1s** | **91.97%** 🎯 | **77.21%** 🚀 | **83.95%** |
| **09-16 长篇实操** | 14:25 (865s) | 410.6s | 413.1s | **372.9s** | **90.27%** 🎯 | **90.82%** 🚀 | **90.54%** |
| **09-16 深度讲解** | 20:25 (1225s) | 350.2s | 340.8s | **304.9s** | **89.45%** 🎯 | **87.07%** 🚀 | **88.25%** |
| **5 工程大盘总计** | **53:21 (3196s)** | **1285.4s** | **1223.7s** | **1115.5s** | **91.16%** 🎯 | **86.78%** 🚀 | **88.92%** |

- **零破坏性（91.2% 精确率）**：字级保护垫确保不吞字、不伤及有效讲解。
- **高性价比与极速**：单部 10 分钟视频耗时约 25~40 秒，商用 API 调用成本低于 ¥0.08 RMB。

---

## 数据边界

- **本地处理**：视频切片、画面重构与硬件渲染 100% 在创作者本机执行，视频画面永不上云。
- **云端服务**：
  - 音频流提取后发送至阿里云百炼 FunAudio ASR 进行字级转录。
  - 转录后的文本片段（含时间戳与停顿标记）送入 Qwen 3.8 Omni Flash 进行语义口误判断。
- **零凭据落盘**：API Key 仅存于系统凭据服务或环境变量，不写入工程目录或代码中。

---

## 开源协议

[MIT](LICENSE) © 2026 oil
