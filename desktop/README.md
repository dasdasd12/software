# KIIIe Control Lab

KIIIe 键盘配置上位机的 Tauri 2 桌面应用。界面使用 Vue 3 + TypeScript，Rust 负责桌面权限边界和 sidecar 生命周期，现有 Python 键盘逻辑继续作为唯一的 Profile 编译与设备通信实现。

## 当前范围

- 使用 CAD 实际坐标渲染 AK Ergo 77、屏幕、旋钮与五向摇杆。
- 读取 `../config/factory_default_profile.json`，支持键位单选、多选、分组选择与映射草稿。
- 启动保持无按键选择；键位、高级行为和诊断页面把“未选择”作为正常可浏览状态。
- Normal、Rapid Trigger 与 Disabled 可按键写入 `control_assignments` 单键覆盖，使用整数千分比保存并保留 0.1% 编辑精度。
- DKS 按规范的整数微米区间写入 `behaviors` 并由当前 binding 引用；SOCD 只向 `interaction_rules` 写入成员和裁决策略。两者的 RuntimeTable / 固件执行能力尚未接入。
- `macro_defs`、`report_rate_policy`、`input_guard_policy` 直接编辑正式 Profile source；对应固件执行能力尚未接入。
- 本地 Profile 库支持新建、复制、Slot 规划、JSON 导入导出，以及工作草稿自动恢复。
- 尚未应用的局部编辑在换键、清空、切页、刷新和 Tauri 原生关闭时都会提示确认；关闭前同步刷新 Profile 恢复草稿。
- 通过本地 JSONL bridge 验证 Profile、连接设备并安装到用户 Slot 1–3。
- Slot 0 始终作为只读 Factory Profile；设备当前槽位和写入目标槽位分别保存。
- 灯效和屏显页面可保存本地预览草稿；设备资源协议待接入。诊断只展示真实设备数据入口，不生成模拟采样；AI 配置暂缓。

## 开发

需要 Node.js、Rust stable、Windows C++ Build Tools / Windows SDK、WebView2，以及 Python 3.11+。

```powershell
cd software\desktop
npm install
& ..\.venv-claude\Scripts\python.exe -m pip install -r scripts\core-requirements.txt
npm run app:dev
```

开发模式优先使用 `software/.venv-claude/Scripts/python.exe`。也可以显式指定：

```powershell
$env:KIIIE_CORE_PYTHON = "C:\path\to\python.exe"
npm run app:dev
```

## 检查与发布

```powershell
npm run check
npm run build

# 无硬件 sidecar 检查
.\scripts\build-sidecar.ps1
.\scripts\smoke-sidecar.ps1

# 构建 Windows x64 NSIS 安装器
npm run app:build
```

`app:build` 会在隔离的 `.sidecar-build` 环境中使用锁定版本的 PyInstaller 和 pyserial，生成 Tauri 所需的 `kiiie-core-x86_64-pc-windows-msvc.exe`，然后打包安装器。可通过 `KIIIE_SIDECAR_PYTHON` 指定构建 Python。

## 架构边界

```text
Vue UI
  -> Tauri invoke / events
Rust bridge supervisor
  -> JSONL over stdin/stdout
Python desktop_bridge
  -> keyboard.akpk + AkpkSerialClient
Keyboard USB CDC
```

前端没有 Shell 权限，也不直接启动 Python。桥接层只暴露白名单方法，并限制请求大小、响应大小和超时。发布版把 Python Core 安装在主程序旁，由 Rust 隐藏启动并在应用退出时回收。
