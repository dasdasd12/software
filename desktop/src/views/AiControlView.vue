<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import type {
  AiPermission,
  AiSession,
  AiTimelineEvent,
} from "../stores/aiMonitor";
import { useAiMonitorStore } from "../stores/aiMonitor";

type InspectorTab = "session" | "approval" | "connection";

const ai = useAiMonitorStore();
const inspectorTab = ref<InspectorTab>("session");

const selectedSession = computed(() => ai.selectedSession);
const activePermission = computed(() => ai.activePermission);
const visibleEvents = computed(() => ai.visibleTimeline.slice(0, 100));
const selectedPermissions = computed(() => {
  if (!ai.selectedSessionId) return ai.permissions;
  return ai.permissions.filter(
    (permission) => permission.sessionId === ai.selectedSessionId,
  );
});

const localCoreMode = computed(() => {
  if (ai.processStatus.managed) return "由 Control Lab 管理";
  if (ai.processStatus.backend === "external" || ai.processStatus.state === "external") {
    return "复用已有 Local Core";
  }
  return "等待启动";
});

function agentLabel(agent: string): string {
  return agent.toLowerCase() === "codex" ? "Codex" : "Claude Code";
}

function sessionStateLabel(state: string): string {
  const labels: Record<string, string> = {
    IDLE: "空闲",
    CONNECTING: "连接中",
    SUBMITTED: "已提交",
    WORKING: "工作中",
    RUNNING: "运行中",
    THINKING: "思考中",
    EXECUTING: "执行工具",
    WAITING_PERMISSION: "等待审批",
    WAITING_INPUT: "等待输入",
    PAUSED: "已暂停",
    COMPLETED: "已完成",
    FAILED: "失败",
    CANCELLED: "已取消",
    ERROR: "错误",
    TIMEOUT: "超时",
    OFFLINE: "离线",
  };
  return labels[state] ?? state;
}

function stateTone(state: string): string {
  if (state === "WAITING_PERMISSION" || state === "WAITING_INPUT") return "amber";
  if (["ERROR", "FAILED", "TIMEOUT"].includes(state)) return "coral";
  if (["WORKING", "RUNNING", "THINKING", "EXECUTING", "SUBMITTED"].includes(state)) return "blue";
  if (state === "COMPLETED") return "green";
  return "";
}

function riskLabel(risk: string): string {
  const labels: Record<string, string> = {
    low: "低风险",
    medium: "中风险",
    high: "高风险",
    critical: "严重风险",
    destructive: "破坏性操作",
  };
  return labels[risk] ?? risk;
}

function riskTone(risk: string): string {
  if (risk === "low") return "green";
  if (risk === "medium") return "amber";
  return "coral";
}

function eventMark(event: AiTimelineEvent): string {
  const marks: Record<AiTimelineEvent["kind"], string> = {
    connection: "LINK",
    session: "RUN",
    state: "STATE",
    message: "OUT",
    tool: "TOOL",
    permission: "ASK",
    decision: "OK",
    error: "ERR",
  };
  return marks[event.kind];
}

function formatClock(timestamp: number): string {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(timestamp);
}

function formatDate(timestamp: number): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(timestamp);
}

function shortSessionId(sessionId: string): string {
  return sessionId.length > 17
    ? `${sessionId.slice(0, 9)}…${sessionId.slice(-5)}`
    : sessionId;
}

function selectSession(session: AiSession): void {
  ai.selectedSessionId = session.sessionId;
  inspectorTab.value = "session";
}

function respond(permission: AiPermission, approved: boolean): void {
  ai.respondToPermission(permission, approved);
}

onMounted(() => void ai.initialize());
</script>

<template>
  <div class="page-shell">
    <section class="page-main ai-main">
      <header class="page-heading">
        <div>
          <p class="page-kicker">CLAUDE CODE / LIVE MONITOR</p>
          <h1>AI 运行监控</h1>
          <p class="lede">Control Lab 启动时自动接通 Local Core，跟随键盘打开的 Claude Code、工具调用和审批状态。</p>
        </div>
        <div class="heading-actions">
          <span class="chip" :class="ai.connected ? 'green' : ai.state === 'error' ? 'coral' : 'amber'">
            <i></i>{{ ai.monitorLabel }}
          </span>
          <button class="button secondary" @click="ai.reconnectNow">重新连接</button>
        </div>
      </header>

      <section class="ai-overview" aria-label="AI 监控概览">
        <article>
          <i class="overview-signal" :class="{ live: ai.connected }"></i>
          <span><small>LOCAL CORE</small><b>{{ ai.connected ? "ONLINE" : "RETRYING" }}</b></span>
          <em>{{ ai.processStatus.pid ? `PID ${ai.processStatus.pid}` : localCoreMode }}</em>
        </article>
        <article>
          <i class="overview-number">{{ ai.activeSessions.length }}</i>
          <span><small>ACTIVE SESSIONS</small><b>{{ ai.activeSessions.length ? "正在运行" : "等待启动" }}</b></span>
          <em>{{ ai.sessions.length }} 个已记录会话</em>
        </article>
        <article :class="{ urgent: ai.permissions.length > 0 }">
          <i class="overview-number">{{ ai.permissions.length }}</i>
          <span><small>APPROVAL QUEUE</small><b>{{ ai.permissions.length ? "需要确认" : "队列为空" }}</b></span>
          <em>{{ ai.permissions.length ? "键盘与本页均可处理" : "审批链路待命" }}</em>
        </article>
      </section>

      <section class="ai-workspace">
        <aside class="ai-session-list">
          <header>
            <span class="tiny-label">AI SESSIONS</span>
            <b>{{ ai.sessions.length }}</b>
          </header>
          <div v-if="ai.sessions.length" class="ai-session-scroll">
            <button
              v-for="session in ai.sessions"
              :key="session.sessionId"
              type="button"
              :class="{ active: ai.selectedSessionId === session.sessionId }"
              @click="selectSession(session)"
            >
              <i :class="stateTone(session.state)"></i>
              <span>
                <b>{{ agentLabel(session.agent) }}</b>
                <small>{{ shortSessionId(session.sessionId) }}</small>
              </span>
              <em>{{ sessionStateLabel(session.state) }}</em>
            </button>
          </div>
          <div v-else class="ai-session-empty">
            <i>F24</i>
            <b>监控已经打开</b>
            <p>按 Fn + 摇杆中键启动 Claude Code；新会话会自动出现在这里。</p>
          </div>
          <footer>
            <i :class="{ live: ai.connected }"></i>
            <span>{{ ai.connected ? "事件流实时更新" : "断线后自动重连" }}</span>
          </footer>
        </aside>

        <section class="ai-live-console">
          <header>
            <div>
              <span class="tiny-label">LIVE SESSION RAIL</span>
              <h2>{{ selectedSession ? agentLabel(selectedSession.agent) : "全部事件" }}</h2>
            </div>
            <span v-if="selectedSession" class="chip" :class="stateTone(selectedSession.state)">
              <i></i>{{ sessionStateLabel(selectedSession.state) }}
            </span>
            <span v-else class="chip">等待会话</span>
          </header>

          <article v-if="activePermission" class="approval-focus">
            <div class="approval-focus__meta">
              <span class="chip" :class="riskTone(activePermission.riskLevel)">
                <i></i>{{ riskLabel(activePermission.riskLevel) }}
              </span>
              <b>{{ activePermission.tool }}</b>
              <em>{{ ai.permissionRemainingSeconds(activePermission) }}s</em>
            </div>
            <p>{{ activePermission.description }}</p>
            <div class="approval-focus__actions">
              <span>Fn + 摇杆上下选择，Fn + 旋钮中键确认</span>
              <button
                class="button danger"
                :disabled="ai.isPermissionResponding(activePermission)"
                @click="respond(activePermission, false)"
              >拒绝</button>
              <button
                class="button primary"
                :disabled="ai.isPermissionResponding(activePermission)"
                @click="respond(activePermission, true)"
              >批准</button>
            </div>
          </article>

          <div v-if="visibleEvents.length" class="ai-trace-scroll">
            <div class="ai-trace-rail">
              <article
                v-for="event in visibleEvents"
                :key="event.id"
                class="ai-trace-event"
                :class="`is-${event.kind}`"
              >
                <i>{{ eventMark(event) }}</i>
                <div>
                  <header><b>{{ event.label }}</b><time>{{ formatClock(event.timestamp) }}</time></header>
                  <p>{{ event.detail || "事件已记录" }}</p>
                </div>
              </article>
            </div>
          </div>

          <div v-else class="ai-trace-empty">
            <span class="trace-cursor"></span>
            <p><b>正在监听 Local Core</b><small>Claude Code 的状态、输出和审批请求会从这里进入。</small></p>
          </div>

          <footer>
            <span><i :class="{ live: ai.connected }"></i>{{ ai.connected ? "LIVE" : "RECONNECTING" }}</span>
            <b>{{ ai.processStatus.url }}</b>
          </footer>
        </section>
      </section>
    </section>

    <aside class="page-inspector">
      <div class="inspector-inner">
        <header class="inspector-head">
          <p class="tiny-label">AI 控制</p>
          <h2>Claude Code <small>MONITOR</small></h2>
          <p>查看键盘启动的会话，并在审批到达时核对真实工具内容。</p>
        </header>
        <nav class="inspector-tabs" role="tablist" aria-label="AI 监控信息">
          <button role="tab" :aria-selected="inspectorTab === 'session'" :class="{ active: inspectorTab === 'session' }" @click="inspectorTab = 'session'">会话</button>
          <button role="tab" :aria-selected="inspectorTab === 'approval'" :class="{ active: inspectorTab === 'approval' }" @click="inspectorTab = 'approval'">审批</button>
          <button role="tab" :aria-selected="inspectorTab === 'connection'" :class="{ active: inspectorTab === 'connection' }" @click="inspectorTab = 'connection'">连接</button>
        </nav>

        <div class="inspector-scroll ai-inspector-scroll">
          <template v-if="inspectorTab === 'session'">
            <section v-if="selectedSession" class="inspector-section">
              <div class="section-title">
                <h3>当前会话</h3>
                <span class="chip" :class="stateTone(selectedSession.state)">{{ sessionStateLabel(selectedSession.state) }}</span>
              </div>
              <dl class="ai-facts">
                <div><dt>Agent</dt><dd>{{ agentLabel(selectedSession.agent) }}</dd></div>
                <div><dt>Session</dt><dd class="mono">{{ selectedSession.sessionId }}</dd></div>
                <div><dt>启动方式</dt><dd>{{ selectedSession.launchSurface || "由 Local Core 接管" }}</dd></div>
                <div><dt>控制通道</dt><dd>{{ selectedSession.controlMode || "Claude hook" }}</dd></div>
                <div><dt>最后更新</dt><dd>{{ formatDate(selectedSession.updatedAt) }}</dd></div>
              </dl>
            </section>
            <section v-if="selectedSession?.lastMessage" class="inspector-section">
              <div class="section-title"><h3>最近输出</h3><span>TAIL</span></div>
              <pre class="ai-message-tail">{{ selectedSession.lastMessage }}</pre>
            </section>
            <section v-else class="inspector-section">
              <div class="notice">选择一个会话后，这里显示它的来源、状态和最近输出。监控不会接管终端输入。</div>
            </section>
          </template>

          <template v-else-if="inspectorTab === 'approval'">
            <section class="inspector-section">
              <div class="section-title"><h3>待处理审批</h3><span>{{ selectedPermissions.length }} 项</span></div>
              <div v-if="selectedPermissions.length" class="ai-permission-list">
                <article v-for="permission in selectedPermissions" :key="`${permission.sessionId}-${permission.requestId}`">
                  <header>
                    <b>{{ permission.tool }}</b>
                    <span class="chip" :class="riskTone(permission.riskLevel)">{{ riskLabel(permission.riskLevel) }}</span>
                  </header>
                  <p>{{ permission.description }}</p>
                  <footer>
                    <span>{{ ai.permissionRemainingSeconds(permission) }} 秒后超时</span>
                    <button :disabled="ai.isPermissionResponding(permission)" @click="respond(permission, false)">拒绝</button>
                    <button :disabled="ai.isPermissionResponding(permission)" @click="respond(permission, true)">批准</button>
                  </footer>
                </article>
              </div>
              <div v-else class="notice">当前没有等待审批的 Claude Code 操作。</div>
            </section>
            <section class="inspector-section">
              <div class="notice warning">屏幕与本页显示同一条审批请求。无论审批键当前走 USB、RF 还是 BLE，最终结果都由 Local Core 统一记录。</div>
            </section>
          </template>

          <template v-else>
            <section class="inspector-section">
              <div class="section-title"><h3>Local Core</h3><span class="chip" :class="ai.connected ? 'green' : 'amber'">{{ ai.processStatus.state }}</span></div>
              <dl class="ai-facts">
                <div><dt>运行方式</dt><dd>{{ localCoreMode }}</dd></div>
                <div><dt>Backend</dt><dd>{{ ai.processStatus.backend || "—" }}</dd></div>
                <div><dt>PID</dt><dd class="mono">{{ ai.processStatus.pid || "—" }}</dd></div>
                <div><dt>WebSocket</dt><dd class="mono">{{ ai.processStatus.url }}</dd></div>
                <div><dt>能力</dt><dd>{{ ai.capabilities.length ? ai.capabilities.join(" · ") : "等待握手" }}</dd></div>
              </dl>
            </section>
            <section v-if="ai.lastError || ai.processStatus.lastError" class="inspector-section">
              <div class="notice warning">{{ ai.lastError || ai.processStatus.lastError }}</div>
            </section>
            <section v-if="ai.processStatus.stderrTail.length" class="inspector-section">
              <div class="section-title"><h3>进程输出</h3><span>STDERR</span></div>
              <pre class="ai-message-tail">{{ ai.processStatus.stderrTail.slice(-8).join("\n") }}</pre>
            </section>
          </template>
        </div>

        <div class="inspector-actions">
          <button class="button quiet" @click="ai.refreshProcessStatus">刷新状态</button>
          <button class="button primary" @click="ai.requestSnapshot">同步快照</button>
        </div>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.ai-main { padding-bottom: 12px; }

.ai-overview {
  flex: none;
  min-height: 66px;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin-top: 2px;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.ai-overview article {
  min-width: 0;
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr);
  grid-template-rows: auto auto;
  align-content: center;
  gap: 4px 10px;
  padding: 0 16px;
  border-right: 1px solid var(--line);
}

.ai-overview article:first-child { padding-left: 4px; }
.ai-overview article:last-child { border-right: 0; }
.ai-overview article.urgent { background: linear-gradient(90deg, transparent, var(--amber-soft)); }
.ai-overview span { display: grid; gap: 4px; }
.ai-overview small { color: var(--muted); font: 7px/1 var(--font-utility); letter-spacing: .1em; }
.ai-overview b { font-size: 10px; }
.ai-overview em { grid-column: 2; color: var(--muted); font-size: 8px; font-style: normal; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.overview-signal {
  position: relative;
  grid-row: 1 / span 2;
  width: 24px;
  height: 24px;
  align-self: center;
  border: 1px solid var(--line-strong);
  border-radius: 50%;
}

.overview-signal::before,
.overview-signal::after {
  content: "";
  position: absolute;
  border-radius: 50%;
}

.overview-signal::before { inset: 6px; background: var(--muted-2); }
.overview-signal::after { inset: -1px; border: 1px solid transparent; }
.overview-signal.live::before { background: var(--mint); }
.overview-signal.live::after { border-color: color-mix(in srgb, var(--mint) 35%, transparent); animation: ai-pulse 1.8s ease-out infinite; }

.overview-number {
  grid-row: 1 / span 2;
  width: 28px;
  align-self: center;
  color: var(--accent-strong);
  font: 600 20px/1 var(--font-display);
  font-style: normal;
  text-align: center;
}

.ai-workspace {
  min-height: 0;
  flex: 1;
  display: grid;
  grid-template-columns: 196px minmax(0, 1fr);
  gap: 0;
  margin-top: 10px;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: rgba(255,255,255,.58);
}

.ai-session-list {
  min-height: 0;
  display: grid;
  grid-template-rows: 47px minmax(0, 1fr) 38px;
  border-right: 1px solid var(--line);
  background: rgba(249,250,252,.84);
}

.ai-session-list > header,
.ai-session-list > footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 13px;
  border-bottom: 1px solid var(--line);
}

.ai-session-list > header b {
  width: 20px;
  height: 20px;
  display: grid;
  place-items: center;
  border-radius: 6px;
  color: var(--accent-strong);
  background: var(--accent-soft);
  font: 8px/1 var(--font-utility);
}

.ai-session-scroll { min-height: 0; padding: 7px; overflow-y: auto; }

.ai-session-scroll button {
  width: 100%;
  min-height: 58px;
  display: grid;
  grid-template-columns: 7px minmax(0, 1fr) auto;
  align-items: center;
  gap: 9px;
  padding: 7px 8px;
  color: var(--ink-soft);
  text-align: left;
  border-radius: 9px;
  background: transparent;
}

.ai-session-scroll button.active { color: var(--accent-strong); background: var(--accent-soft); }
.ai-session-scroll button > i { width: 6px; height: 28px; border-radius: 999px; background: var(--line-strong); }
.ai-session-scroll button > i.blue { background: var(--accent); }
.ai-session-scroll button > i.green { background: var(--mint); }
.ai-session-scroll button > i.amber { background: var(--amber); }
.ai-session-scroll button > i.coral { background: var(--coral); }
.ai-session-scroll span { min-width: 0; display: grid; gap: 4px; }
.ai-session-scroll b { font-size: 9px; }
.ai-session-scroll small { color: var(--muted); font: 7px/1 var(--font-utility); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ai-session-scroll em { color: var(--muted); font-size: 7px; font-style: normal; white-space: nowrap; }

.ai-session-empty {
  display: grid;
  align-content: center;
  justify-items: start;
  gap: 9px;
  padding: 20px;
}

.ai-session-empty > i {
  width: 38px;
  height: 32px;
  display: grid;
  place-items: center;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  color: var(--accent-strong);
  background: white;
  box-shadow: 0 3px 0 var(--line);
  font: 700 8px/1 var(--font-utility);
  font-style: normal;
}

.ai-session-empty b { font-size: 10px; }
.ai-session-empty p { color: var(--muted); font-size: 8px; line-height: 1.6; }
.ai-session-list > footer { justify-content: flex-start; gap: 7px; color: var(--muted); font-size: 7px; border-top: 1px solid var(--line); border-bottom: 0; }
.ai-session-list > footer i,
.ai-live-console > footer i { width: 5px; height: 5px; border-radius: 50%; background: var(--muted-2); }
.ai-session-list > footer i.live,
.ai-live-console > footer i.live { background: var(--mint); box-shadow: 0 0 0 3px color-mix(in srgb, var(--mint) 16%, transparent); }

.ai-live-console {
  min-width: 0;
  min-height: 0;
  display: grid;
  grid-template-rows: 54px auto minmax(0, 1fr) 34px;
  color: #e8edf8;
  background:
    radial-gradient(circle at 88% 10%, rgba(91,111,255,.18), transparent 28%),
    linear-gradient(160deg, #17202f, #101722 72%);
}

.ai-live-console > header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 15px;
  padding: 0 17px;
  border-bottom: 1px solid rgba(216,225,240,.1);
}

.ai-live-console > header > div { display: grid; gap: 5px; }
.ai-live-console > header .tiny-label { color: #8290a7; }
.ai-live-console > header h2 { font: 600 13px/1 var(--font-display); }
.ai-live-console .chip { color: #dce4f2; background: rgba(255,255,255,.08); }
.ai-live-console .chip.blue { color: #aeb9ff; background: rgba(91,111,255,.14); }
.ai-live-console .chip.green { color: #85dfc2; background: rgba(70,198,160,.12); }
.ai-live-console .chip.amber { color: #ffd486; background: rgba(233,169,75,.13); }
.ai-live-console .chip.coral { color: #ff9e8f; background: rgba(255,113,91,.13); }

.approval-focus {
  display: grid;
  gap: 9px;
  margin: 12px 14px 0;
  padding: 12px 13px;
  border: 1px solid rgba(255,196,101,.24);
  border-radius: 10px;
  background: linear-gradient(100deg, rgba(233,169,75,.16), rgba(233,169,75,.07));
}

.approval-focus__meta { display: flex; align-items: center; gap: 8px; }
.approval-focus__meta b { font: 9px/1 var(--font-utility); }
.approval-focus__meta em { margin-left: auto; color: #ffd486; font: 9px/1 var(--font-utility); font-style: normal; }
.approval-focus > p { max-height: 42px; overflow: hidden; color: #f3f5fa; font: 9px/1.55 var(--font-utility); }
.approval-focus__actions { display: flex; align-items: center; gap: 7px; }
.approval-focus__actions > span { flex: 1; color: #aeb8c8; font-size: 7px; }
.approval-focus__actions .button { height: 28px; padding: 0 11px; font-size: 8px; }

.ai-trace-scroll { min-height: 0; overflow-y: auto; }
.ai-trace-rail { position: relative; display: grid; gap: 0; padding: 12px 16px 18px 17px; }
.ai-trace-rail::before { content: ""; position: absolute; left: 34px; top: 18px; bottom: 24px; width: 1px; background: linear-gradient(#5f72ff, rgba(125,143,170,.16)); }

.ai-trace-event {
  position: relative;
  display: grid;
  grid-template-columns: 35px minmax(0, 1fr);
  gap: 10px;
  min-height: 51px;
  padding: 4px 0 8px;
}

.ai-trace-event > i {
  position: relative;
  z-index: 1;
  width: 33px;
  height: 20px;
  display: grid;
  place-items: center;
  border: 3px solid #151e2b;
  border-radius: 7px;
  color: #aeb9ff;
  background: #28365a;
  font: 6px/1 var(--font-utility);
  font-style: normal;
  letter-spacing: .04em;
}

.ai-trace-event.is-permission > i { color: #ffd486; background: #5a4523; }
.ai-trace-event.is-decision > i { color: #83dfc1; background: #204d42; }
.ai-trace-event.is-error > i { color: #ff9e8f; background: #5b302d; }
.ai-trace-event.is-tool > i { color: #d6b5ff; background: #43305c; }
.ai-trace-event > div { min-width: 0; padding-top: 2px; }
.ai-trace-event header { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.ai-trace-event b { color: #edf1f8; font-size: 9px; }
.ai-trace-event time { color: #6f7e93; font: 7px/1 var(--font-utility); }
.ai-trace-event p { margin-top: 5px; color: #aab5c6; font: 8px/1.5 var(--font-utility); white-space: pre-wrap; overflow-wrap: anywhere; }

.ai-trace-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  color: #aeb8c8;
}

.ai-trace-empty p { display: grid; gap: 5px; }
.ai-trace-empty b { color: #edf1f8; font-size: 10px; }
.ai-trace-empty small { font-size: 8px; }
.trace-cursor { width: 8px; height: 16px; background: #8292ff; animation: ai-cursor 1.1s steps(1) infinite; }

.ai-live-console > footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 15px;
  color: #708096;
  border-top: 1px solid rgba(216,225,240,.09);
  font: 7px/1 var(--font-utility);
}

.ai-live-console > footer span { display: flex; align-items: center; gap: 7px; color: #8e9cb0; }
.ai-live-console > footer b { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 400; }
.ai-inspector-scroll { overflow-y: auto; }

.ai-facts { display: grid; margin: 0; }
.ai-facts div { min-height: 37px; display: grid; grid-template-columns: 76px minmax(0, 1fr); align-items: center; gap: 9px; border-bottom: 1px solid var(--line); }
.ai-facts div:last-child { border-bottom: 0; }
.ai-facts dt { color: var(--muted); font-size: 8px; }
.ai-facts dd { min-width: 0; margin: 0; color: var(--ink-soft); font-size: 8px; text-align: right; overflow: hidden; text-overflow: ellipsis; }

.ai-message-tail {
  max-height: 160px;
  margin: 0;
  padding: 10px;
  overflow: auto;
  border-radius: 8px;
  color: #dce5f2;
  background: var(--screen);
  font: 7px/1.55 var(--font-utility);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.ai-permission-list { display: grid; gap: 8px; }
.ai-permission-list article { display: grid; gap: 8px; padding: 10px; border: 1px solid var(--line); border-radius: 9px; background: var(--paper-soft); }
.ai-permission-list header,
.ai-permission-list footer { display: flex; align-items: center; gap: 6px; }
.ai-permission-list header b { font: 9px/1 var(--font-utility); }
.ai-permission-list header .chip { margin-left: auto; }
.ai-permission-list p { max-height: 50px; overflow: hidden; color: var(--ink-soft); font: 8px/1.5 var(--font-utility); }
.ai-permission-list footer span { flex: 1; color: var(--muted); font-size: 7px; }
.ai-permission-list footer button { padding: 0; color: var(--accent-strong); background: transparent; font-size: 8px; font-weight: 700; }
.ai-permission-list footer button:first-of-type { color: #b94e3e; }
.ai-permission-list footer button:disabled { opacity: .45; }

@keyframes ai-pulse {
  from { transform: scale(.75); opacity: .75; }
  to { transform: scale(1.55); opacity: 0; }
}

@keyframes ai-cursor {
  0%, 45% { opacity: 1; }
  46%, 100% { opacity: 0; }
}

@media (prefers-reduced-motion: reduce) {
  .overview-signal.live::after,
  .trace-cursor { animation: none; }
}

@media (max-width: 1260px) {
  .ai-overview em { display: none; }
  .ai-overview article { grid-template-rows: auto; }
  .ai-overview article > i { grid-row: 1; }
  .ai-workspace { grid-template-columns: 174px minmax(0, 1fr); }
}
</style>
