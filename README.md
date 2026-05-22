# CH32H417 AI Keyboard Software

这是 AI Keyboard 项目的软件仓库，当前目标是把本地核心服务、键盘配置、设备传输适配器和主流 AI Agent CLI 串成一个可迭代的 MVP。

当前 MVP 的软件模型：

```text
Browser/Desktop UI, tests, automation
  Local WebSocket API
Local Core Service MVP
  Codex CLI / Claude Code CLI
  DeviceTransport abstraction
Keyboard device
  USB Vendor HID / CDC / BLE GATT / dongle vendor channel
```

Local Core Service MVP 负责维护会话、启动或恢复 Codex/Claude 进程、处理权限请求，并把 Agent 状态投影给本地 UI 和未来的设备原生 transport。WebSocket 只用于本地 UI、测试和自动化客户端；它不是键盘固件协议。

## 仓库结构

```text
src/
  bridge/          Local Core Service MVP 和本地 WebSocket API
  devices/         设备传输 frame/transport 抽象与模拟 transport
  device/          CH32H417 设备端 AI Agent 协议与会话管理模块
docs/
  pre_design_report/  前期硬件、网络、产品调研资料
  user_manual/        芯片、屏幕等器件资料
scripts/
  monitor-bridge.ps1  本地桥接服务和依赖健康检查
  local-api-smoke.py  本地 Local Core WebSocket API smoke 脚本
skills/
  wch-mrs-automation/ WCH MounRiver Studio 自动化辅助技能
```

## Local Core Service 快速启动

本机已配置项目 Conda 环境：

```powershell
conda activate "D:\UserData\My Documents\AI Keyboard\.conda-envs\ai-keyboard"
```

其中已安装 Python 3.11、Node.js/npm、Claude Code、Codex CLI、Local Core Service 依赖和 pytest。

如需从零重建环境，可在仓库根目录执行：

```powershell
& "D:\Program Files\miniconda3\Scripts\conda.exe" create -y -p "D:\UserData\My Documents\AI Keyboard\.conda-envs\ai-keyboard" python=3.11 nodejs
conda activate "D:\UserData\My Documents\AI Keyboard\.conda-envs\ai-keyboard"
pip install -r src\bridge\requirements.txt pytest
npm install -g @anthropic-ai/claude-code @openai/codex
```

启动 Local Core Service MVP：

```powershell
cd src\bridge
python server.py --config config.yaml
```

默认监听地址是 `ws://127.0.0.1:8765`。Local API 是高权限本地接口，`0.0.0.0` 只应用于明确的本地调试。本地检查可以运行：

```powershell
.\scripts\monitor-bridge.ps1
```

`config.yaml` 中的 `agents` 控制 Claude Code 和 Codex 的启用状态、可执行文件路径、环境变量和额外参数。Local Core Service 会自动为 Claude 添加 `-p --output-format stream-json --verbose`，为 Codex 添加 `exec --json`，因此 `args` 只应填写额外参数。

## 设备端模块

`src/devices/device_transport.py` 定义软件侧 transport-independent 的 `DeviceFrame`、设备能力、设备状态和 `SimulatedTransport`。真实键盘后续应通过 USB Vendor HID、CDC、BLE GATT 或 2.4G dongle vendor channel 接入。

`src/device/agent_protocol.h` 是当前设备端 C 模块使用的旧 MVP 消息模型，后续会逐步迁移到 compact device frame 和 slot mapping；它不再代表 Local API WebSocket wire shape。

`src/device/agent_manager.c` 维护设备端本地 session cache，处理旧 MVP 消息模型里的任务状态、增量消息、权限请求和错误事件，并通过 UI 回调通知屏幕层。

`src/device/agent_launcher.c` 提供键盘快捷键入口，用于切换当前 Agent、启动或恢复任务、确认权限和取消任务。目前 UI、LittleFS 持久化和真实权限 request_id 仍是待接入项。

## 当前状态

已完成：

- Python Local Core Service MVP 骨架。
- Codex/Claude 事件到统一设备消息的基础转换。
- 设备端 Agent 协议、会话缓存和快捷键路由骨架。
- 设备传输 `DeviceFrame` / `SimulatedTransport` 软件侧骨架。
- 本地监控脚本。

已知问题和近期重点：

- `SessionManager.create()` 的死锁已在首轮稳定化中修复，并补充测试。
- `permission_response` 已完成 MVP 级 request tracking 和 ACK；真实 CLI stdin 转发仍待实现。
- 设备端 session list 解析已完成 MVP；LittleFS 持久化和完整 UI 回调仍是占位实现。
- 需要继续把 Local API、核心状态和设备 transport 分层，避免 UI WebSocket 成为固件协议。

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
- Local API WebSocket 收发、错误响应、session list、权限 ACK 和 client 断开清理。
- SimulatedTransport frame round trip、capability/status、payload boundary 和 closed transport error。

本地 API smoke 脚本可用于手动联调已经启动的 Local Core Service：

```powershell
python scripts\local-api-smoke.py --scenario basic
python scripts\local-api-smoke.py --scenario real-agent --agent codex --context "say hello"
python scripts\local-api-smoke.py --scenario real-agent --agent claude --context "say hello"
```

`basic` 只验证本地 WebSocket API 入口；`real-agent` 会尝试启动真实 Codex 或 Claude Code，依赖本机 CLI 登录态和可执行文件路径，因此只作为手动 smoke 检查。

## 开发节奏

近期开发计划见 [docs/development_plan.md](docs/development_plan.md)。当前优先级是先让 Local Core Service 稳定、可测、只监听本地，再推进 DeviceTransport、设备协议投影和 UI/LVGL 集成。
