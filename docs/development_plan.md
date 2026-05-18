# AI Keyboard Software 开发计划

本计划面向 `software` 仓库。近期原则是先稳定 PC 端 Bridge Server，再把设备端协议闭环、屏幕 UI 和真实硬件联调逐步接上。

## Phase 0: 文档与可运行基线

目标：让新开发者能理解项目、安装依赖、启动桥接服务，并知道当前风险。

交付项：

- 新增开发者优先的 `README.md`。
- 新增 `src/bridge/requirements.txt`，声明 Bridge Server 运行依赖。
- 修复 `scripts/monitor-bridge.ps1` 的硬编码路径和 Codex 版本检测语法错误。
- 明确当前 MVP 架构：CH32H417 设备端通过 WebSocket/JSON Lines 连接 PC Bridge Server，Bridge Server 代理 Codex/Claude CLI。

验收：

- `README.md` 覆盖目录结构、启动命令、监控脚本、当前状态和已知问题。
- 监控脚本能从任意仓库路径推导 `$Workspace`。
- `python -m py_compile src/bridge/*.py` 通过。

## Phase 1: Bridge Server 稳定化

目标：先把本地桥接服务变成稳定、可测的开发基线。

交付项：

- 修复 `SessionManager.create()` 死锁：在已持有 `_lock` 时调用 `_enforce_limit(locked=True)`。
- 保持 `_enforce_limit()` 外部调用兼容，`gc()` 继续使用 `locked=True`。
- 修复 `_handle_device()` 异常路径，保证 sender task 在设备断线和异常时都会被取消并等待结束。
- 梳理 `AgentProxy` 命令构造，避免 `config.yaml` 中的额外参数与内建 `--json`、`--output-format stream-json` 重复。
- 增加 session manager 和 protocol unifier 的最小 pytest 覆盖。

验收：

- `SessionManager().create(AgentType.CODEX)` 1 秒内返回，不再挂死。
- 超过 `max_sessions` 时淘汰最旧 session。
- `gc()` 删除超过保留时间的终态 session。
- Codex 和 Claude 的基础 delta、完成、失败事件能转换成统一设备消息。
- `pytest` 通过。

## Phase 2: 设备端协议闭环

目标：让设备端 C 模块和 Bridge Server 的交互从骨架走向真实闭环。

交付项：

- Bridge Server 记录 `permission_request` 的 `request_id -> session_id -> proxy` 映射。（MVP 已完成）
- `permission_response` 按 request id 返回 `permission_ack`，并预留真实 Agent 转发接口。（MVP 已完成）
- 设备端保存真实 pending request id，快捷键确认/取消使用最新 request id。（MVP 已完成）
- 实现 `session_list` payload 解析，把服务端 session 列表合并到设备端 cache。
- 为 LittleFS 持久化定义最小读写接口，先落地 active agent 和 session metadata。

验收：

- 设备端收到权限请求后可以批准或拒绝，Bridge Server 能路由到对应 Agent。
- `list_sessions` 能刷新设备端 session cache。
- 重启后 active agent 能恢复。

## Phase 3: 产品功能扩展

目标：把稳定的协议链路接到真实产品体验。

交付项：

- 接入 LVGL 状态页、增量消息视图和权限弹窗。
- 完善快捷键策略：Agent 切换、启动/恢复、打断、权限确认。
- 扩展 Codex/Claude 协议适配，覆盖工具调用、错误、等待输入、长任务进度。
- 增加真实 CLI 和模拟设备 WebSocket 的集成测试。
- 准备硬件联调清单，覆盖以太网、USB RNDIS、屏幕刷新和键盘事件路由。

验收：

- 模拟设备可以完成启动 Agent、接收增量消息、处理权限请求和打断任务。
- 真实设备可以稳定显示会话状态并触发快捷键控制。
- Bridge Server 长时间运行后没有明显 task 泄漏或 session 状态漂移。

## 当前默认决策

- README 和开发计划使用中文，面向开发者。
- 首轮优先级是 Bridge Server 稳定化。
- 本仓库暂不修改 `hardware` 项目。
- 设备端 JSON 消息类型和字段保持兼容，后续新增能力优先在现有消息上补齐行为。
