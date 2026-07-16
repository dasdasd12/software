import { computed, ref } from "vue";
import { defineStore } from "pinia";
import {
  readLocalCoreStatus,
  startLocalCoreMonitor,
  type LocalCoreProcessStatus,
} from "../services/localCore";
import { runningInTauri } from "../services/bridge";

type JsonRecord = Record<string, unknown>;

export type AiMonitorState =
  | "idle"
  | "starting"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "error";

export interface AiSession {
  sessionId: string;
  agent: string;
  state: string;
  createdAt: number;
  updatedAt: number;
  launchSurface?: string;
  controlMode?: string;
  processPid?: number;
  lastMessage: string;
  currentTool?: string;
}

export interface AiPermission {
  requestId: string;
  sessionId?: string;
  agent: string;
  tool: string;
  description: string;
  riskLevel: string;
  timeoutSec: number;
  createdAt: number;
  native?: JsonRecord;
}

export interface AiTimelineEvent {
  id: string;
  sessionId?: string;
  kind: "connection" | "session" | "state" | "message" | "tool" | "permission" | "decision" | "error";
  label: string;
  detail: string;
  timestamp: number;
}

const DEFAULT_LOCAL_CORE_STATUS: LocalCoreProcessStatus = {
  state: "stopped",
  managed: false,
  pid: null,
  url: "ws://127.0.0.1:8765",
  backend: null,
  lastError: null,
  stderrTail: [],
};

const ACTIVE_SESSION_STATES = new Set([
  "CONNECTING",
  "SUBMITTED",
  "WORKING",
  "RUNNING",
  "THINKING",
  "EXECUTING",
  "WAITING_PERMISSION",
  "WAITING_INPUT",
  "PAUSED",
]);
const ACTIVE_SESSION_FRESHNESS_MS = 2 * 60 * 60 * 1_000;

function record(value: unknown): JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as JsonRecord
    : {};
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function numeric(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function epochMillis(value: unknown, fallback = Date.now()): number {
  const numberValue = numeric(value, 0);
  if (!numberValue) return fallback;
  return numberValue > 10_000_000_000 ? numberValue : numberValue * 1_000;
}

function makeId(prefix: string): string {
  return typeof crypto.randomUUID === "function"
    ? `${prefix}-${crypto.randomUUID()}`
    : `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export const useAiMonitorStore = defineStore("ai-monitor", () => {
  const state = ref<AiMonitorState>("idle");
  const processStatus = ref<LocalCoreProcessStatus>({ ...DEFAULT_LOCAL_CORE_STATUS });
  const sessions = ref<AiSession[]>([]);
  const permissions = ref<AiPermission[]>([]);
  const timeline = ref<AiTimelineEvent[]>([]);
  const selectedSessionId = ref<string | null>(null);
  const capabilities = ref<string[]>([]);
  const lastError = ref<string | null>(null);
  const respondingPermissionKeys = ref<string[]>([]);
  const now = ref(Date.now());

  const connected = computed(() => state.value === "connected");
  const activeSessions = computed(() =>
    sessions.value.filter(
      (session) => ACTIVE_SESSION_STATES.has(session.state)
        && Date.now() - session.updatedAt < ACTIVE_SESSION_FRESHNESS_MS,
    ),
  );
  const selectedSession = computed(() =>
    sessions.value.find((session) => session.sessionId === selectedSessionId.value) ?? null,
  );
  const visibleTimeline = computed(() => {
    if (!selectedSessionId.value) return timeline.value;
    return timeline.value.filter(
      (event) => !event.sessionId || event.sessionId === selectedSessionId.value,
    );
  });
  const activePermission = computed(() => {
    if (selectedSessionId.value) {
      const scoped = permissions.value.find(
        (permission) => permission.sessionId === selectedSessionId.value,
      );
      if (scoped) return scoped;
    }
    return permissions.value[0] ?? null;
  });
  const monitorLabel = computed(() => {
    if (state.value === "connected") return "实时监控已连接";
    if (state.value === "starting") return "正在启动 Local Core";
    if (state.value === "connecting" || state.value === "reconnecting") return "正在连接监控";
    if (state.value === "error") return "监控连接异常";
    return "监控尚未启动";
  });

  const clientKind = runningInTauri() ? "desktop-ui" : "browser-dev-ui";
  const clientId = `kiiie-control-lab-${Math.random().toString(16).slice(2, 10)}`;
  const seenEventIds = new Set<string>();

  let socket: WebSocket | null = null;
  let initialized = false;
  let disposed = false;
  let reconnectAttempt = 0;
  let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
  let snapshotTimer: ReturnType<typeof setInterval> | undefined;
  let clockTimer: ReturnType<typeof setInterval> | undefined;

  function selectBestSession(): void {
    if (
      selectedSessionId.value
      && sessions.value.some((session) => session.sessionId === selectedSessionId.value)
    ) {
      return;
    }
    selectedSessionId.value = activePermission.value?.sessionId
      ?? activeSessions.value[0]?.sessionId
      ?? sessions.value[0]?.sessionId
      ?? null;
  }

  function normalizeSession(value: unknown): AiSession | null {
    const input = record(value);
    const sessionId = text(input.session_id || input.sessionId);
    if (!sessionId) return null;
    const existing = sessions.value.find((session) => session.sessionId === sessionId);
    const updatedAt = epochMillis(input.updated_at || input.updatedAt, Date.now());
    const stateValue = text(input.state, existing?.state ?? "IDLE").toUpperCase();
    return {
      sessionId,
      agent: text(input.agent || input.provider_id || input.providerId, existing?.agent ?? "claude"),
      state: ACTIVE_SESSION_STATES.has(stateValue)
        && Date.now() - updatedAt >= ACTIVE_SESSION_FRESHNESS_MS
        ? "OFFLINE"
        : stateValue,
      createdAt: epochMillis(input.created_at || input.createdAt, existing?.createdAt ?? Date.now()),
      updatedAt,
      launchSurface: text(
        input.launch_surface || input.launchSurface,
        existing?.launchSurface ?? "",
      ) || undefined,
      controlMode: text(
        input.control_mode || input.controlMode,
        existing?.controlMode ?? "",
      ) || undefined,
      processPid: numeric(input.process_pid || input.processPid, existing?.processPid ?? 0) || undefined,
      lastMessage: existing?.lastMessage ?? "",
      currentTool: existing?.currentTool,
    };
  }

  function mergeSession(value: unknown): AiSession | null {
    const normalized = normalizeSession(value);
    if (!normalized) return null;
    const index = sessions.value.findIndex(
      (session) => session.sessionId === normalized.sessionId,
    );
    if (index >= 0) sessions.value[index] = normalized;
    else sessions.value.push(normalized);
    sessions.value.sort((left, right) => right.updatedAt - left.updatedAt);
    selectBestSession();
    return normalized;
  }

  function normalizePermission(value: unknown): AiPermission | null {
    const input = record(value);
    const requestId = text(input.request_id || input.permission_id || input.requestId);
    if (!requestId) return null;
    const sessionId = text(input.session_id || input.sessionId) || undefined;
    const existing = permissions.value.find(
      (permission) => permission.requestId === requestId
        && permission.sessionId === sessionId,
    );
    const incomingNative = record(input.native);
    const mergedNative = {
      ...(existing?.native ?? {}),
      ...incomingNative,
    };
    return {
      requestId,
      sessionId,
      agent: text(input.agent || input.provider_id || input.providerId, "claude"),
      tool: text(input.tool || input.action_type || input.actionType, "unknown"),
      description: text(input.description || input.summary, "等待用户审批"),
      riskLevel: text(input.risk_level || input.riskLevel, "medium").toLowerCase(),
      timeoutSec: Math.max(1, numeric(input.timeout_sec || input.timeoutSec, 30)),
      createdAt: epochMillis(
        input.created_at || input.createdAt || input.timestamp,
        existing?.createdAt ?? Date.now(),
      ),
      native: Object.keys(mergedNative).length ? mergedNative : undefined,
    };
  }

  function upsertPermission(value: unknown): AiPermission | null {
    const normalized = normalizePermission(value);
    if (!normalized) return null;
    const index = permissions.value.findIndex(
      (permission) => permission.requestId === normalized.requestId
        && permission.sessionId === normalized.sessionId,
    );
    if (index >= 0) permissions.value[index] = normalized;
    else permissions.value.push(normalized);
    permissions.value.sort((left, right) => left.createdAt - right.createdAt);
    if (normalized.sessionId) {
      mergeSession({
        session_id: normalized.sessionId,
        agent: normalized.agent,
        state: "WAITING_PERMISSION",
        updated_at: Date.now(),
      });
      selectedSessionId.value = normalized.sessionId;
    }
    return normalized;
  }

  function removePermission(requestId: string, sessionId?: string): void {
    permissions.value = permissions.value.filter((permission) =>
      permission.requestId !== requestId
      || (sessionId !== undefined && permission.sessionId !== sessionId),
    );
    const key = permissionKey(requestId, sessionId);
    respondingPermissionKeys.value = respondingPermissionKeys.value.filter((candidate) =>
      sessionId === undefined
        ? !candidate.endsWith(`\u0000${requestId}`)
        : candidate !== key,
    );
  }

  function addTimelineEvent(event: Omit<AiTimelineEvent, "id"> & { id?: string }): void {
    const id = event.id ?? makeId("trace");
    if (seenEventIds.has(id)) return;

    if (event.kind === "message") {
      const latest = timeline.value[0];
      if (
        latest
        && latest.kind === "message"
        && latest.sessionId === event.sessionId
        && event.timestamp - latest.timestamp < 2_000
      ) {
        latest.detail = `${latest.detail}${event.detail}`.slice(-2_400);
        latest.timestamp = event.timestamp;
        return;
      }
    }

    seenEventIds.add(id);
    timeline.value.unshift({ ...event, id });
    if (timeline.value.length > 160) {
      for (const removed of timeline.value.splice(160)) seenEventIds.delete(removed.id);
    }
  }

  function applySnapshot(value: unknown): void {
    const snapshot = record(value);
    const snapshotSessions = record(snapshot.sessions);
    const nextSessions = Object.values(snapshotSessions)
      .map(normalizeSession)
      .filter((session): session is AiSession => session !== null)
      .sort((left, right) => right.updatedAt - left.updatedAt);
    sessions.value = nextSessions;

    const snapshotPermissions = Array.isArray(snapshot.permissions)
      ? snapshot.permissions
      : [];
    permissions.value = snapshotPermissions
      .map(normalizePermission)
      .filter((permission): permission is AiPermission => permission !== null)
      .sort((left, right) => left.createdAt - right.createdAt);
    selectBestSession();
  }

  function coreEventLabel(eventType: string, payload: JsonRecord): [string, string] {
    if (eventType === "agent.session.created") {
      return ["会话已建立", `${text(payload.agent, "Claude")} · ${text(payload.session_id)}`];
    }
    if (eventType === "agent.session.state_changed") {
      return ["状态更新", text(payload.state, "UNKNOWN")];
    }
    if (eventType === "agent.permission.requested") {
      return ["等待审批", text(payload.description || payload.summary, text(payload.tool, "tool"))];
    }
    if (eventType === "agent.permission.resolved") {
      const approved = payload.approved === true || text(payload.decision) === "approve";
      return [approved ? "审批通过" : "审批拒绝", text(payload.request_id)];
    }
    if (eventType === "agent.cli.launched") {
      return ["Claude Code 已打开", text(payload.workspace || payload.session_id, "前台终端")];
    }
    if (eventType === "agent.run.interrupted") {
      return ["运行已中断", text(payload.session_id)];
    }
    return [eventType.replace(/^agent\./, ""), text(payload.message || payload.summary)];
  }

  function handleCoreEvent(value: unknown): void {
    const event = record(value);
    const eventType = text(event.type);
    const payload = record(event.payload);
    const sessionId = text(payload.session_id || record(event.target).session_id) || undefined;
    const timestamp = epochMillis(event.timestamp);

    if (eventType === "agent.session.created" || eventType === "agent.session.state_changed") {
      mergeSession(payload);
    } else if (eventType === "agent.session.exited") {
      mergeSession({
        ...payload,
        state: text(payload.state, "OFFLINE"),
        updated_at: event.timestamp,
      });
    } else if (eventType === "agent.permission.requested") {
      upsertPermission(payload);
    } else if (eventType === "agent.permission.resolved") {
      removePermission(text(payload.request_id), sessionId);
    }

    if (eventType !== "system.snapshot.generated") {
      const [label, detail] = coreEventLabel(eventType, payload);
      addTimelineEvent({
        id: text(event.event_id) || undefined,
        sessionId,
        kind: eventType.includes("permission")
          ? eventType.endsWith("resolved") ? "decision" : "permission"
          : eventType.includes("state") ? "state" : "session",
        label,
        detail,
        timestamp,
      });
    }
  }

  function handleMessage(value: unknown): void {
    const message = record(value);
    const type = text(message.type);
    const sessionId = text(message.session_id || message.sessionId) || undefined;
    const timestamp = epochMillis(message.timestamp);

    if (type === "hello_ack") {
      capabilities.value = Array.isArray(message.capabilities)
        ? message.capabilities.filter((item): item is string => typeof item === "string")
        : [];
      state.value = "connected";
      lastError.value = null;
      reconnectAttempt = 0;
      addTimelineEvent({
        kind: "connection",
        label: "监控已连接",
        detail: `${text(message.client_kind, clientKind)} · ${processStatus.value.url}`,
        timestamp,
      });
      requestSessions();
      requestSnapshot();
      startSnapshotTimer();
      return;
    }

    if (type === "session_list") {
      const items = Array.isArray(message.sessions) ? message.sessions : [];
      for (const item of items) mergeSession(item);
      return;
    }

    if (type === "snapshot") {
      applySnapshot(message.snapshot);
      return;
    }

    if (type === "event") {
      handleCoreEvent(message.event);
      return;
    }

    if (type === "task_update") {
      mergeSession({
        session_id: sessionId,
        agent: message.agent,
        state: message.state,
        updated_at: timestamp,
      });
      addTimelineEvent({
        sessionId,
        kind: "state",
        label: "运行状态",
        detail: text(message.state, "UNKNOWN"),
        timestamp,
      });
      return;
    }

    if (type === "agent_message_delta") {
      const delta = text(message.delta);
      const session = mergeSession({
        session_id: sessionId,
        agent: message.agent,
        state: "WORKING",
        updated_at: timestamp,
      });
      if (session && delta) {
        session.lastMessage = `${session.lastMessage}${delta}`.slice(-4_000);
        addTimelineEvent({
          sessionId,
          kind: "message",
          label: "Claude 输出",
          detail: delta,
          timestamp,
        });
      }
      return;
    }

    if (type === "task_completed" || type === "task_error" || type === "task_failed") {
      mergeSession({
        session_id: sessionId,
        agent: message.agent,
        state: type === "task_completed" ? "COMPLETED" : "ERROR",
        updated_at: timestamp,
      });
      addTimelineEvent({
        sessionId,
        kind: type === "task_completed" ? "state" : "error",
        label: type === "task_completed" ? "本轮完成" : "运行错误",
        detail: text(message.summary || message.error_message),
        timestamp,
      });
      return;
    }

    if (type === "agent_hook_event") {
      const tool = text(message.tool, "Claude hook");
      const session = mergeSession({
        session_id: sessionId,
        agent: message.agent,
        updated_at: timestamp,
      });
      if (session) session.currentTool = tool;
      addTimelineEvent({
        sessionId,
        kind: "tool",
        label: text(message.hook_event_name, "工具事件"),
        detail: tool,
        timestamp,
      });
      return;
    }

    if (type === "permission_request") {
      const permission = upsertPermission(message);
      if (permission) {
        addTimelineEvent({
          id: `permission-${permission.sessionId ?? "none"}-${permission.requestId}`,
          sessionId: permission.sessionId,
          kind: "permission",
          label: `等待审批 · ${permission.tool}`,
          detail: permission.description,
          timestamp: permission.createdAt,
        });
      }
      return;
    }

    if (type === "permission_ack") {
      const requestId = text(message.request_id);
      const approved = message.approved === true;
      removePermission(requestId, sessionId);
      addTimelineEvent({
        sessionId,
        kind: "decision",
        label: approved ? "已批准" : "已拒绝",
        detail: requestId,
        timestamp,
      });
      requestSnapshot();
      return;
    }

    if (type === "interaction_request") {
      addTimelineEvent({
        sessionId,
        kind: "permission",
        label: "Claude 等待输入",
        detail: text(message.interaction_type || message.tool_name),
        timestamp,
      });
      return;
    }

    if (type === "error") {
      lastError.value = `${text(message.code, "LOCAL_CORE_ERROR")}: ${text(message.message)}`;
      addTimelineEvent({
        sessionId,
        kind: "error",
        label: text(message.code, "Local Core 错误"),
        detail: text(message.message),
        timestamp,
      });
    }
  }

  function send(message: JsonRecord): boolean {
    if (!socket || socket.readyState !== WebSocket.OPEN) return false;
    socket.send(JSON.stringify(message));
    return true;
  }

  function requestSessions(): void {
    send({ type: "list_sessions", agent: "all", timestamp: Math.floor(Date.now() / 1_000) });
  }

  function requestSnapshot(): void {
    send({
      type: "command",
      command: {
        command_id: makeId("cmd-snapshot"),
        type: "system.snapshot.request",
        source: { kind: clientKind, client_id: clientId },
        payload: {},
        timestamp: Math.floor(Date.now() / 1_000),
      },
    });
  }

  function startSnapshotTimer(): void {
    if (snapshotTimer) clearInterval(snapshotTimer);
    snapshotTimer = setInterval(() => requestSnapshot(), 2_500);
  }

  function stopSocketTimers(): void {
    if (snapshotTimer) clearInterval(snapshotTimer);
    snapshotTimer = undefined;
  }

  function scheduleReconnect(): void {
    if (disposed || reconnectTimer) return;
    state.value = "reconnecting";
    const delay = Math.min(6_000, 700 * (2 ** Math.min(reconnectAttempt, 4)));
    reconnectAttempt += 1;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = undefined;
      void ensureLocalCoreAndConnect();
    }, delay);
  }

  function connectSocket(): void {
    if (disposed) return;
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      return;
    }

    state.value = reconnectAttempt ? "reconnecting" : "connecting";
    const nextSocket = new WebSocket(processStatus.value.url || "ws://127.0.0.1:8765");
    socket = nextSocket;

    nextSocket.addEventListener("open", () => {
      if (socket !== nextSocket) return;
      send({
        type: "hello",
        client_kind: clientKind,
        client_id: clientId,
        capabilities: ["agent:launch", "permission:respond", "session:list"],
        timestamp: Math.floor(Date.now() / 1_000),
      });
    });

    nextSocket.addEventListener("message", (event) => {
      try {
        handleMessage(JSON.parse(String(event.data)));
      } catch (error) {
        lastError.value = error instanceof Error ? error.message : String(error);
      }
    });

    nextSocket.addEventListener("close", () => {
      if (socket !== nextSocket) return;
      socket = null;
      stopSocketTimers();
      if (!disposed) {
        lastError.value = "Local Core WebSocket 已断开，正在自动重连";
        scheduleReconnect();
      }
    });

    nextSocket.addEventListener("error", () => {
      if (socket === nextSocket && state.value !== "connected") {
        lastError.value = "无法连接 Local Core；监控会自动重试";
      }
    });
  }

  async function ensureLocalCoreAndConnect(): Promise<void> {
    if (disposed) return;
    try {
      processStatus.value = await startLocalCoreMonitor();
      if (processStatus.value.lastError) lastError.value = processStatus.value.lastError;
    } catch (error) {
      processStatus.value = {
        ...DEFAULT_LOCAL_CORE_STATUS,
        state: "unavailable",
        lastError: error instanceof Error ? error.message : String(error),
      };
      lastError.value = processStatus.value.lastError ?? "Local Core 启动失败";
    }
    connectSocket();
  }

  async function initialize(): Promise<void> {
    if (initialized) return;
    initialized = true;
    disposed = false;
    state.value = "starting";
    clockTimer = setInterval(() => {
      now.value = Date.now();
    }, 1_000);
    await ensureLocalCoreAndConnect();
  }

  async function reconnectNow(): Promise<void> {
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = undefined;
    reconnectAttempt = 0;
    socket?.close();
    socket = null;
    stopSocketTimers();
    state.value = "starting";
    await ensureLocalCoreAndConnect();
  }

  async function refreshProcessStatus(): Promise<void> {
    try {
      processStatus.value = await readLocalCoreStatus();
    } catch (error) {
      lastError.value = error instanceof Error ? error.message : String(error);
    }
  }

  function respondToPermission(permission: AiPermission, approved: boolean): void {
    const key = permissionKey(permission.requestId, permission.sessionId);
    if (respondingPermissionKeys.value.includes(key)) return;
    const sent = send({
      type: "permission_response",
      request_id: permission.requestId,
      session_id: permission.sessionId,
      approved,
      timestamp: Math.floor(Date.now() / 1_000),
    });
    if (sent) respondingPermissionKeys.value.push(key);
  }

  function permissionKey(requestId: string, sessionId?: string): string {
    return `${sessionId ?? ""}\u0000${requestId}`;
  }

  function isPermissionResponding(permission: AiPermission): boolean {
    return respondingPermissionKeys.value.includes(
      permissionKey(permission.requestId, permission.sessionId),
    );
  }

  function permissionRemainingSeconds(permission: AiPermission): number {
    return Math.max(
      0,
      Math.ceil((permission.createdAt + permission.timeoutSec * 1_000 - now.value) / 1_000),
    );
  }

  function dispose(): void {
    disposed = true;
    initialized = false;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    if (clockTimer) clearInterval(clockTimer);
    reconnectTimer = undefined;
    clockTimer = undefined;
    stopSocketTimers();
    socket?.close();
    socket = null;
    state.value = "idle";
  }

  return {
    state,
    processStatus,
    sessions,
    permissions,
    timeline,
    selectedSessionId,
    capabilities,
    lastError,
    respondingPermissionKeys,
    now,
    connected,
    activeSessions,
    selectedSession,
    visibleTimeline,
    activePermission,
    monitorLabel,
    initialize,
    reconnectNow,
    refreshProcessStatus,
    requestSnapshot,
    respondToPermission,
    isPermissionResponding,
    permissionRemainingSeconds,
    dispose,
  };
});
