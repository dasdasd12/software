# CH32H417 AI Keyboard Software

这是 AI Keyboard 项目的软件仓库，当前目标是把 CH32H417 键盘终端、PC 端桥接服务和主流 AI Agent CLI 串成一个可迭代的 MVP。

当前 MVP 的通信模型：

```text
CH32H417 设备端
  WebSocket / JSON Lines
PC Bridge Server
  Codex CLI / Claude Code CLI
```

设备端负责按键、屏幕、会话状态和权限确认等交互。PC 端 Bridge Server 负责维护会话、启动或恢复 Codex/Claude 进程，并把不同 Agent 的事件转换成统一 JSON 消息发回设备。

## 仓库结构

```text
src/
  bridge/          PC 端 Python WebSocket 桥接服务
  device/          CH32H417 设备端 AI Agent 协议与会话管理模块
docs/
  pre_design_report/  前期硬件、网络、产品调研资料
  user_manual/        芯片、屏幕等器件资料
scripts/
  monitor-bridge.ps1  本地桥接服务和依赖健康检查
skills/
  wch-mrs-automation/ WCH MounRiver Studio 自动化辅助技能
```

## Bridge Server 快速启动

本机已配置项目 Conda 环境：

```powershell
conda activate "D:\UserData\My Documents\AI Keyboard\.conda-envs\ai-keyboard"
```

其中已安装 Python 3.11、Node.js/npm、Claude Code、Codex CLI、Bridge Server 依赖和 pytest。

如需从零重建环境，可在仓库根目录执行：

```powershell
& "D:\Program Files\miniconda3\Scripts\conda.exe" create -y -p "D:\UserData\My Documents\AI Keyboard\.conda-envs\ai-keyboard" python=3.11 nodejs
conda activate "D:\UserData\My Documents\AI Keyboard\.conda-envs\ai-keyboard"
pip install -r src\bridge\requirements.txt pytest
npm install -g @anthropic-ai/claude-code @openai/codex
```

启动 Bridge Server：

```powershell
cd src\bridge
python server.py --config config.yaml
```

默认监听地址是 `ws://0.0.0.0:8765`。本地检查可以运行：

```powershell
.\scripts\monitor-bridge.ps1
```

`config.yaml` 中的 `agents` 控制 Claude Code 和 Codex 的启用状态、可执行文件路径、环境变量和额外参数。桥接服务会自动为 Claude 添加 `-p --output-format stream-json`，为 Codex 添加 `exec --json`，因此 `args` 只应填写额外参数。

## 设备端模块

`src/device/agent_protocol.h` 定义设备和 Bridge Server 之间的统一消息类型、Agent 类型、任务状态和字段长度上限。

`src/device/agent_manager.c` 维护设备端本地 session cache，处理 Bridge Server 发来的任务状态、增量消息、权限请求和错误事件，并通过 UI 回调通知屏幕层。

`src/device/agent_launcher.c` 提供键盘快捷键入口，用于切换当前 Agent、启动或恢复任务、确认权限和取消任务。目前 UI、LittleFS 持久化和真实权限 request_id 仍是待接入项。

## 当前状态

已完成：

- Python Bridge Server 骨架。
- Codex/Claude 事件到统一设备消息的基础转换。
- 设备端 Agent 协议、会话缓存和快捷键路由骨架。
- 本地监控脚本。

已知问题和近期重点：

- `SessionManager.create()` 的死锁已在首轮稳定化中修复，并补充测试。
- `permission_response` 已完成 MVP 级 request tracking 和 ACK；真实 CLI stdin 转发仍待实现。
- 设备端 session list 解析、LittleFS 持久化和 UI 回调仍是占位实现。
- 需要持续补充 Bridge Server 的异步 WebSocket 测试和真实 CLI 联调。

## 测试

安装测试依赖后，在仓库根目录运行：

```powershell
pip install pytest
pytest
```

当前测试重点覆盖：

- session 创建不阻塞。
- LRU session 淘汰。
- 终态 session 清理。
- Codex/Claude 基础事件转换。

## 开发节奏

近期开发计划见 [docs/development_plan.md](docs/development_plan.md)。当前优先级是先让 Bridge Server 稳定、可测、可本地启动，再推进设备端协议闭环和 UI/LVGL 集成。
