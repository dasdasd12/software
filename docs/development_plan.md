# AI Keyboard Software 开发计划

本计划面向 `software` 仓库。近期原则是先把当前桥接脚本收敛为 Local Core Service MVP，再把 Local API、Agent 适配、设备 transport、屏幕 UI 和真实硬件联调逐步接上。

## Phase 0: 文档与可运行基线

目标：让新开发者能理解项目、安装依赖、启动桥接服务，并知道当前风险。

交付项：

- 新增开发者优先的 `README.md`。
- 新增 `src/bridge/requirements.txt`，声明 Local Core Service 运行依赖。
- 修复 `scripts/monitor-bridge.ps1` 的硬编码路径和 Codex 版本检测语法错误。
- 明确当前 MVP 架构：Local Core Service MVP 代理 Codex/Claude CLI；WebSocket 是本地 UI/test/automation API，不是键盘固件协议。

验收：

- `README.md` 覆盖目录结构、启动命令、监控脚本、当前状态和已知问题。
- 监控脚本能从任意仓库路径推导 `$Workspace`。
- `python -m py_compile src/bridge/*.py` 通过。

## Phase 1: Local Core Service MVP 稳定化

目标：先把本地核心服务变成稳定、可测、只监听本机的开发基线。

交付项：

- 修复 `SessionManager.create()` 死锁：在已持有 `_lock` 时调用 `_enforce_limit(locked=True)`。
- 保持 `_enforce_limit()` 外部调用兼容，`gc()` 继续使用 `locked=True`。
- 修复 Local API WebSocket 异常路径，保证 sender task 在 client 断线和异常时都会被取消并等待结束。
- 梳理 `AgentProxy` 命令构造，避免 `config.yaml` 中的额外参数与内建 `--json`、`--output-format stream-json` 重复。
- 增加 session manager 和 protocol unifier 的最小 pytest 覆盖。
- 将 WebSocket 侧命名从 device 语义收敛为 local API client 语义，并默认绑定 `127.0.0.1`。

验收：

- `SessionManager().create(AgentType.CODEX)` 1 秒内返回，不再挂死。
- 超过 `max_sessions` 时淘汰最旧 session。
- `gc()` 删除超过保留时间的终态 session。
- Codex 和 Claude 的基础 delta、完成、失败事件能转换成统一设备消息。
- `pytest` 通过。
- Local API WebSocket E2E 覆盖非法 JSON、未知消息、session list、fake agent 事件广播、权限 ACK 和 client 断开清理。（MVP 已完成）

## Phase 1.5: DeviceTransport 边界校正

目标：建立设备原生 transport 和本地 UI WebSocket 之间的代码边界，避免 UI JSON 成为固件协议。

交付项：

- 新增 `DeviceFrame`、`DeviceCapabilities`、`DeviceStatus` 和 `DeviceTransport` 软件侧协议接口。
- 新增 `SimulatedTransport`，只做内存 frame round trip，不走 WebSocket。
- 保留 Local API smoke 脚本用于本地服务调试，明确它不是 firmware/device protocol。

验收：

- `DeviceFrame` encode/decode 保留 `frame_type`、`payload`、`protocol_version`、`generation`、`device_id`。
- `SimulatedTransport` 覆盖 open/close、send/read、capability/status、payload size boundary 和 closed transport error。
- `python -m pytest` 通过。

## Phase 2: 设备端协议闭环

目标：让设备端 C 模块和 Local Core Service 的交互从旧 MVP JSON 骨架走向真实 transport/frame 闭环。

交付项：

- Local Core Service 记录 `permission_request` 的 `request_id -> session_id -> proxy` 映射。（MVP 已完成）
- `permission_response` 按 request id 返回 `permission_ack`，并预留真实 Agent 转发接口。（MVP 已完成）
- 设备端保存真实 pending request id，快捷键确认/取消使用最新 request id。（MVP 已完成）
- 实现 `session_list` payload 解析，把服务端 session 列表合并到设备端 cache。（MVP 已完成）
- 为 LittleFS 持久化定义最小读写接口，先落地 active agent 和 session metadata。

验收：

- 设备端收到权限请求后可以批准或拒绝，Local Core Service 能路由到对应 Agent。
- `list_sessions` 能刷新设备端 session cache。
- 重启后 active agent 能恢复。

## Phase 3: 产品功能扩展

目标：把稳定的协议链路接到真实产品体验。

交付项：

- 接入 LVGL 状态页、增量消息视图和权限弹窗。
- 完善快捷键策略：Agent 切换、启动/恢复、打断、权限确认。
- 扩展 Codex/Claude 协议适配，覆盖工具调用、错误、等待输入、长任务进度。
- 增加真实 CLI 和模拟设备 WebSocket 的集成测试。
- 保留真实 CLI 联调为手动 smoke，默认测试使用 fake proxy，避免依赖登录态和网络。
- 准备硬件联调清单，覆盖以太网、USB RNDIS、屏幕刷新和键盘事件路由。

验收：

- 模拟设备可以完成启动 Agent、接收增量消息、处理权限请求和打断任务。
- 真实设备可以稳定显示会话状态并触发快捷键控制。
- Local Core Service 长时间运行后没有明显 task 泄漏或 session 状态漂移。

## 当前默认决策

- README 和开发计划使用中文，面向开发者。
- 首轮优先级是 Local Core Service 稳定化。
- 本仓库暂不修改 `hardware` 项目。
- Local API JSON 消息保持兼容；真实设备协议后续迁移到 compact frame、slot mapping 和 transport capability negotiation。
