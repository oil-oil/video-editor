# API Key 配置与业务读取

已有宿主安全配置或环境注入 `DASHSCOPE_API_KEY` 时直接复用。首次在桌面填写或更换 Key 时使用随附统一页面，不能用“已支持环境变量”或终端隐藏输入代替页面接入。只为外部服务配置；离线处理和本地工具不要求额外 Key。

## 首次配置

将当前 SKILL.md 所在绝对目录记为 `SKILL_DIR`。页面需要 Node.js 22.18+，首次在组件目录安装锁定依赖：

```bash
npm --prefix "$SKILL_DIR/scripts/credential-ui" ci --ignore-scripts
node "$SKILL_DIR/scripts/credential-ui/src/profile.ts" status default
node "$SKILL_DIR/scripts/credential-ui/src/profile.ts" setup default
```

先查 status：退出码 0 表示当前业务凭据可读取，2 表示缺失，1 表示配置或系统后端失败。缺失或用户要求更换时才启动 setup，把返回的本机链接展示给用户，由用户亲自填写保存。不要自动操作真实 Key 页面，不让用户贴进聊天。

页面不回填原值；已有项留空保留，替换需要用户确认。只把 `saved` 当作全部保存成功；`partial`、超时和中断后先重新查状态，再补未完成项。配置成功仅证明保存和可读取，实际 API 可用性以业务调用为准。

## 服务与用途绑定

| 配置名 | 业务环境变量 | 系统凭据引用 | 说明 |
| --- | --- | --- | --- |
| default | `DASHSCOPE_API_KEY` | `video-editor/dashscope/default` | 阿里云百炼 DashScope API Key（仅用于语音转录） |

## 运行业务

推荐使用统一入口 `bash "$SKILL_DIR/scripts/run.sh" cut /path/to/video.mp4`，它选择虚拟环境并复用已有环境变量或 bl 配置，缺少时调用系统凭据注入。该 Key 只给 ASR 使用；语义判断由调用 Skill 的 Agent 在本地完成。底层等价命令如下，`--` 后必须保留 `cut` 子命令：

```bash
node "$SKILL_DIR/scripts/credential-ui/src/profile.ts" run default -- "$SKILL_DIR/.venv/bin/python3" "$SKILL_DIR/scripts/video_editor.py" cut /path/to/video.mp4
```

上述底层注入入口中，环境变量优先；缺失时仅从系统库读取当前配置所需的 Key，并只注入可信业务子进程。参数、普通文件和状态输出都不含 Key。使用页面保存的凭据后，后续云端业务命令同样经统一入口执行。`review` 和 `render` 直接使用本地计划，无需 Key。

系统后端分别为 macOS 钥匙串、Windows 凭据管理器、Linux Secret Service。CI、容器与远程服务器使用已有 Secret 注入，不把本机页面开放到网络。
