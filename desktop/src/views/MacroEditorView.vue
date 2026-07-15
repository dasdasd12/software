<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import type {
  MacroDefinition,
  MacroStep,
} from "../domain/profile/types";
import { useProfileStore } from "../stores/profile";

type TrackId = "press" | "release" | "wait" | "system";
type InspectorTab = "step" | "macro" | "test";
type MacroOperation = MacroStep["op"];

interface TimelineStep {
  index: number;
  track: TrackId;
  label: string;
  detail: string;
  tone: "blue" | "purple" | "light" | "wait" | "coral";
}

const profile = useProfileStore();
const activeMacroId = ref<string | null>(null);
const activeStepIndex = ref<number | null>(null);
const inspectorTab = ref<InspectorTab>("step");
const pendingMacroId = ref("");
const playheadStep = ref(0);
const isPlaying = ref(false);
const deleteArmed = ref(false);
const localNotice = ref("Profile 草稿已就绪；选择或新建一个宏开始编辑");
let playbackTimer: ReturnType<typeof setInterval> | undefined;

const tracks: Array<{ id: TrackId; label: string }> = [
  { id: "press", label: "按下" },
  { id: "release", label: "释放" },
  { id: "wait", label: "延迟" },
  { id: "system", label: "其他" },
];

const operationOptions: Array<{ value: MacroOperation; label: string }> = [
  { value: "key_down", label: "键盘按下" },
  { value: "key_up", label: "键盘释放" },
  { value: "consumer_down", label: "媒体按下" },
  { value: "consumer_up", label: "媒体释放" },
  { value: "pointer_step", label: "鼠标步进" },
  { value: "delay_ticks", label: "延迟 ticks" },
  { value: "wait_release", label: "等待来源释放" },
  { value: "stop", label: "停止宏" },
];

const commonUsages = [
  "keyboard.a",
  "keyboard.c",
  "keyboard.v",
  "keyboard.enter",
  "keyboard.left_ctrl",
  "keyboard.left_shift",
  "keyboard.left_gui",
  "consumer.mute",
  "consumer.volume_increment",
  "consumer.volume_decrement",
];

const macroDefs = computed(() => profile.draftDocument.macro_defs);
const sourceMacroDefs = computed(() => profile.sourceDocument.macro_defs);
const macroEntries = computed(() => Object.entries(macroDefs.value));
const activeMacro = computed<MacroDefinition | undefined>(() =>
  activeMacroId.value ? macroDefs.value[activeMacroId.value] : undefined,
);
const activeStep = computed<MacroStep | undefined>(() => {
  if (!activeMacro.value || activeStepIndex.value === null) return undefined;
  return activeMacro.value.steps[activeStepIndex.value];
});

const timelineSteps = computed<TimelineStep[]>(() =>
  (activeMacro.value?.steps ?? []).map((step, index) => ({
    index,
    track: trackForStep(step),
    label: labelForStep(step),
    detail: detailForStep(step),
    tone: toneForStep(step),
  })),
);

const totalSteps = computed(() =>
  macroEntries.value.reduce((sum, [, macro]) => sum + macro.steps.length, 0),
);
const activeDelayTicks = computed(() =>
  (activeMacro.value?.steps ?? []).reduce(
    (sum, step) => sum + (step.op === "delay_ticks" ? step.ticks : 0),
    0,
  ),
);
const macroDraftDirty = computed(() =>
  JSON.stringify(macroDefs.value) !== JSON.stringify(sourceMacroDefs.value),
);
const activeReferenceCount = computed(() =>
  activeMacroId.value ? macroReferenceCount(activeMacroId.value) : 0,
);
const validationIssues = computed(() => {
  const issues: string[] = [];
  for (const [macroId, macro] of macroEntries.value) {
    macro.steps.forEach((step, index) => {
      const stepName = `${macroId} / STEP ${index + 1}`;
      if ("usage" in step && !step.usage.trim()) issues.push(`${stepName} 缺少 usage`);
      if (step.op === "delay_ticks" && (!Number.isInteger(step.ticks) || step.ticks < 1)) {
        issues.push(`${stepName} 的 ticks 必须是正整数`);
      }
      if (step.op === "wait_release" && !step.source.trim()) issues.push(`${stepName} 缺少 source`);
    });
  }
  return issues;
});

const rulerTicks = computed(() => {
  const count = timelineSteps.value.length;
  if (!count) return [{ label: "—", position: 0 }];
  const visibleCount = Math.min(count, 6);
  const indexes = Array.from({ length: visibleCount }, (_, tick) =>
    visibleCount === 1 ? 0 : Math.round((tick * (count - 1)) / (visibleCount - 1)),
  );
  return [...new Set(indexes)].map((index) => ({
    label: `STEP ${String(index + 1).padStart(2, "0")}`,
    position: ((index + 0.5) / count) * 100,
  }));
});

const formattedPlayhead = computed(() => {
  const count = activeMacro.value?.steps.length ?? 0;
  if (!count) return "STEP — / —";
  const current = Math.min(count, playheadStep.value + 1);
  return `STEP ${String(current).padStart(2, "0")} / ${String(count).padStart(2, "0")}`;
});

const activeUsage = computed({
  get: () => activeStep.value && "usage" in activeStep.value ? activeStep.value.usage : "",
  set: (value: string) => {
    if (!activeStep.value || !("usage" in activeStep.value)) return;
    activeStep.value.usage = value;
    touchProfile("步骤 usage 已更新到 Profile 草稿");
  },
});

const activeDelay = computed({
  get: () => activeStep.value?.op === "delay_ticks" ? activeStep.value.ticks : 1,
  set: (value: number) => {
    if (activeStep.value?.op !== "delay_ticks") return;
    activeStep.value.ticks = Math.max(1, Math.round(Number(value) || 1));
    touchProfile("延迟 ticks 已更新到 Profile 草稿");
  },
});

const activeWaitSource = computed({
  get: () => activeStep.value?.op === "wait_release" ? activeStep.value.source : "",
  set: (value: string) => {
    if (activeStep.value?.op !== "wait_release") return;
    activeStep.value.source = value;
    touchProfile("等待来源已更新到 Profile 草稿");
  },
});

function pointerValue(field: "dx" | "dy" | "wheel"): number {
  return activeStep.value?.op === "pointer_step" ? activeStep.value[field] : 0;
}

function setPointerValue(field: "dx" | "dy" | "wheel", event: Event): void {
  if (activeStep.value?.op !== "pointer_step") return;
  const value = Number((event.target as HTMLInputElement).value);
  activeStep.value[field] = Number.isFinite(value) ? Math.round(value) : 0;
  touchProfile("鼠标步进参数已更新到 Profile 草稿");
}

watch(macroEntries, (entries) => {
  if (activeMacroId.value && !entries.some(([id]) => id === activeMacroId.value)) {
    activeMacroId.value = null;
    activeStepIndex.value = null;
    pendingMacroId.value = "";
    stopPlayback();
  }
});

watch(activeStep, (step) => {
  if (!step) {
    playheadStep.value = 0;
    return;
  }
  playheadStep.value = activeStepIndex.value ?? 0;
});

function trackForStep(step: MacroStep): TrackId {
  if (step.op === "key_down" || step.op === "consumer_down") return "press";
  if (step.op === "key_up" || step.op === "consumer_up") return "release";
  if (step.op === "delay_ticks") return "wait";
  return "system";
}

function toneForStep(step: MacroStep): TimelineStep["tone"] {
  if (step.op === "key_down") return "blue";
  if (step.op === "consumer_down") return "purple";
  if (step.op === "key_up" || step.op === "consumer_up") return "light";
  if (step.op === "delay_ticks") return "wait";
  return "coral";
}

function shortUsage(usage: string): string {
  const [, value = usage] = usage.split(".", 2);
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function labelForStep(step: MacroStep): string {
  if ("usage" in step) return shortUsage(step.usage) || "未设置";
  if (step.op === "delay_ticks") return `${step.ticks} ticks`;
  if (step.op === "pointer_step") return `${step.dx}, ${step.dy} · W${step.wheel}`;
  if (step.op === "wait_release") return step.source || "等待释放";
  return "STOP";
}

function detailForStep(step: MacroStep): string {
  if ("usage" in step) return `${step.op} · ${step.usage}`;
  if (step.op === "delay_ticks") return `delay_ticks · ${step.ticks}`;
  if (step.op === "pointer_step") return `pointer_step · dx ${step.dx} / dy ${step.dy} / wheel ${step.wheel}`;
  if (step.op === "wait_release") return `wait_release · ${step.source}`;
  return "stop";
}

function eventStyle(index: number): Record<string, string> {
  const count = Math.max(1, timelineSteps.value.length);
  const slotWidth = 100 / count;
  const gap = Math.min(1.2, slotWidth * 0.12);
  const left = index * slotWidth + gap;
  const width = Math.min(100 - left, Math.max(0.18, slotWidth - gap * 2));
  return { left: `${left}%`, width: `${width}%` };
}

function playheadStyle(): Record<string, string> {
  const count = activeMacro.value?.steps.length ?? 0;
  if (!count) return { left: "0%" };
  return { left: `${Math.min(100, Math.max(0, ((playheadStep.value + 0.5) / count) * 100))}%` };
}

function macroColor(index: number): "blue" | "coral" | "mint" {
  return (["blue", "coral", "mint"] as const)[index % 3];
}

function selectMacro(id: string): void {
  stopPlayback();
  activeMacroId.value = id;
  activeStepIndex.value = null;
  pendingMacroId.value = id;
  inspectorTab.value = "macro";
  deleteArmed.value = false;
  localNotice.value = `已选择 ${id}；尚未选择任何步骤`;
}

function selectStep(index: number): void {
  activeStepIndex.value = index;
  playheadStep.value = index;
  inspectorTab.value = "step";
  deleteArmed.value = false;
}

function stopPlayback(): void {
  isPlaying.value = false;
  if (playbackTimer !== undefined) {
    clearInterval(playbackTimer);
    playbackTimer = undefined;
  }
}

function togglePlayback(): void {
  const count = activeMacro.value?.steps.length ?? 0;
  if (!count) return;
  if (isPlaying.value) {
    stopPlayback();
    return;
  }
  if (playheadStep.value >= count - 1) playheadStep.value = 0;
  isPlaying.value = true;
  playbackTimer = setInterval(() => {
    if (playheadStep.value >= count - 1) {
      stopPlayback();
      return;
    }
    playheadStep.value += 1;
  }, 360);
}

function seek(delta: number): void {
  stopPlayback();
  const max = Math.max(0, (activeMacro.value?.steps.length ?? 1) - 1);
  playheadStep.value = Math.min(max, Math.max(0, playheadStep.value + delta));
}

function createStep(op: MacroOperation): MacroStep {
  switch (op) {
    case "key_down": return { op, usage: "keyboard.a" };
    case "key_up": return { op, usage: "keyboard.a" };
    case "consumer_down": return { op, usage: "consumer.mute" };
    case "consumer_up": return { op, usage: "consumer.mute" };
    case "pointer_step": return { op, dx: 0, dy: 0, wheel: 1 };
    case "delay_ticks": return { op, ticks: 50 };
    case "wait_release": return { op, source: "key_000" };
    case "stop": return { op };
  }
}

function addStep(op: MacroOperation): void {
  const macro = activeMacro.value;
  if (!macro) return;
  const insertAt = activeStepIndex.value === null ? macro.steps.length : activeStepIndex.value + 1;
  macro.steps.splice(insertAt, 0, createStep(op));
  activeStepIndex.value = insertAt;
  playheadStep.value = insertAt;
  inspectorTab.value = "step";
  touchProfile(`已新增 STEP ${String(insertAt + 1).padStart(2, "0")} 到 macro_defs`);
}

function changeOperation(event: Event): void {
  const macro = activeMacro.value;
  const index = activeStepIndex.value;
  if (!macro || index === null) return;
  const op = (event.target as HTMLSelectElement).value as MacroOperation;
  macro.steps.splice(index, 1, createStep(op));
  touchProfile(`STEP ${String(index + 1).padStart(2, "0")} 已改为 ${op}`);
}

function moveStep(direction: -1 | 1): void {
  const macro = activeMacro.value;
  const index = activeStepIndex.value;
  if (!macro || index === null) return;
  const target = index + direction;
  if (target < 0 || target >= macro.steps.length) return;
  const [step] = macro.steps.splice(index, 1);
  macro.steps.splice(target, 0, step);
  activeStepIndex.value = target;
  playheadStep.value = target;
  touchProfile(`步骤已移动到 STEP ${String(target + 1).padStart(2, "0")}`);
}

function deleteStep(): void {
  const macro = activeMacro.value;
  const index = activeStepIndex.value;
  if (!macro || index === null) return;
  macro.steps.splice(index, 1);
  activeStepIndex.value = macro.steps.length ? Math.min(index, macro.steps.length - 1) : null;
  playheadStep.value = activeStepIndex.value ?? 0;
  touchProfile("步骤已从 macro_defs 草稿删除");
}

function nextMacroId(): string {
  let sequence = macroEntries.value.length + 1;
  let candidate = `m_macro_${String(sequence).padStart(2, "0")}`;
  while (candidate in macroDefs.value) {
    sequence += 1;
    candidate = `m_macro_${String(sequence).padStart(2, "0")}`;
  }
  return candidate;
}

function createMacro(): void {
  const macroId = nextMacroId();
  macroDefs.value[macroId] = {
    steps: [],
    repeat: "none",
    cancel_on_release: false,
  };
  activeMacroId.value = macroId;
  activeStepIndex.value = null;
  pendingMacroId.value = macroId;
  inspectorTab.value = "macro";
  touchProfile(`${macroId} 已创建在 Profile.macro_defs`);
}

function isValidMacroId(value: string): boolean {
  return /^[a-z][a-z0-9_]{0,47}$/.test(value);
}

function renameMacro(): void {
  const currentId = activeMacroId.value;
  const nextId = pendingMacroId.value.trim();
  if (!currentId || !activeMacro.value) return;
  if (!isValidMacroId(nextId)) {
    localNotice.value = "宏 ID 需以小写字母开头，只能包含小写字母、数字和下划线（最长 48 字符）";
    return;
  }
  if (nextId !== currentId && nextId in macroDefs.value) {
    localNotice.value = `${nextId} 已存在，请使用不同的宏 ID`;
    return;
  }
  if (nextId === currentId) {
    localNotice.value = "宏 ID 没有变化";
    return;
  }

  const renamed: Record<string, MacroDefinition> = {};
  for (const [macroId, macro] of macroEntries.value) {
    renamed[macroId === currentId ? nextId : macroId] = macro;
  }
  profile.draftDocument.macro_defs = renamed;
  renameMacroReferences(currentId, nextId);
  activeMacroId.value = nextId;
  pendingMacroId.value = nextId;
  touchProfile(`宏 ID 已从 ${currentId} 重命名为 ${nextId}，macro_call 引用已同步`);
}

function behaviorDefinitions(): Record<string, unknown> {
  const value = profile.draftDocument.behaviors;
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function macroReferenceCount(macroId: string): number {
  return Object.values(behaviorDefinitions()).filter((value) => {
    if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
    const behavior = value as Record<string, unknown>;
    return behavior.kind === "macro_call" && behavior.macro_id === macroId;
  }).length;
}

function renameMacroReferences(currentId: string, nextId: string): void {
  for (const value of Object.values(behaviorDefinitions())) {
    if (typeof value !== "object" || value === null || Array.isArray(value)) continue;
    const behavior = value as Record<string, unknown>;
    if (behavior.kind === "macro_call" && behavior.macro_id === currentId) behavior.macro_id = nextId;
  }
}

function deleteMacro(): void {
  const macroId = activeMacroId.value;
  if (!macroId) return;
  if (activeReferenceCount.value > 0) {
    localNotice.value = `${macroId} 仍被 ${activeReferenceCount.value} 个 macro_call behavior 引用，请先解除引用`;
    return;
  }
  if (!deleteArmed.value) {
    deleteArmed.value = true;
    localNotice.value = `再次点击“确认删除宏”将从 Profile 草稿删除 ${macroId}`;
    return;
  }
  delete macroDefs.value[macroId];
  activeMacroId.value = null;
  activeStepIndex.value = null;
  pendingMacroId.value = "";
  deleteArmed.value = false;
  stopPlayback();
  touchProfile(`${macroId} 已从 Profile.macro_defs 草稿删除`);
}

function toggleCancelOnRelease(): void {
  if (!activeMacro.value) return;
  activeMacro.value.cancel_on_release = !activeMacro.value.cancel_on_release;
  touchProfile(`cancel_on_release 已设为 ${activeMacro.value.cancel_on_release}`);
}

function touchProfile(message: string): void {
  profile.compileState = "idle";
  profile.compileMessage = "宏草稿已变化，等待重新验证";
  localNotice.value = message;
}

onBeforeUnmount(stopPlayback);
</script>

<template>
  <div class="page-shell">
    <section class="page-main macro-main">
      <header class="page-heading">
        <div>
          <p class="page-kicker">PROFILE / MACRO DEFS</p>
          <h1>宏编辑器</h1>
          <p class="lede">直接编辑 Profile 的确定性步骤序列；步骤顺序、延迟 ticks 与 macro_call 引用保持同一份真源。</p>
        </div>
        <div class="heading-actions">
          <button class="button secondary" disabled title="键盘事件捕获协议待接入">● 录制待接入</button>
          <button class="button primary" @click="createMacro">新建宏</button>
        </div>
      </header>

      <section class="macro-workspace">
        <aside class="macro-list">
          <header><span>PROFILE.MACRO_DEFS</span><b>{{ macroEntries.length }}</b></header>
          <button
            v-for="([macroId, macro], index) in macroEntries"
            :key="macroId"
            :class="{ active: macroId === activeMacroId }"
            @click="selectMacro(macroId)"
          >
            <i class="macro-dot" :class="macroColor(index)"></i>
            <span><b>{{ macroId }}</b><small>{{ macro.steps.length }} 步 · {{ macro.steps.reduce((sum, step) => sum + (step.op === 'delay_ticks' ? step.ticks : 0), 0) }} ticks</small></span>
          </button>
          <div v-if="!macroEntries.length" class="macro-empty-list">
            <b>当前 Profile 没有宏</b>
            <span>Factory 的 macro_defs 为空；新建后会直接进入草稿。</span>
          </div>
          <div class="macro-limits">
            <span>当前 Profile 用量</span>
            <p><b>{{ macroEntries.length }}</b> 个宏</p>
            <p><b>{{ totalSteps }}</b> 个步骤</p>
            <small>最终上限以后续固件 capability 为准</small>
          </div>
        </aside>

        <div
          v-if="activeMacro && activeMacroId"
          class="timeline-editor"
          :style="{ '--step-grid': `${100 / Math.max(1, activeMacro.steps.length)}%` }"
        >
          <header class="timeline-head">
            <div>
              <span class="key-token selected">M{{ macroEntries.findIndex(([id]) => id === activeMacroId) + 1 }}</span>
              <p><b>{{ activeMacroId }}</b><small>{{ activeReferenceCount }} 个引用 · Profile 草稿{{ macroDraftDirty ? '有变更' : '未变化' }}</small></p>
            </div>
            <div class="transport">
              <button aria-label="上一个步骤" @click="seek(-1)">↶</button>
              <button class="play" :disabled="!activeMacro.steps.length" :aria-label="isPlaying ? '暂停步骤预览' : '播放步骤预览'" @click="togglePlayback">{{ isPlaying ? 'Ⅱ' : '▶' }}</button>
              <button aria-label="下一个步骤" @click="seek(1)">↷</button>
              <span>{{ formattedPlayhead }}</span>
            </div>
          </header>

          <div class="timeline-ruler">
            <i aria-hidden="true"></i>
            <div class="timeline-ruler-lane step-ruler">
              <span v-for="tick in rulerTicks" :key="`${tick.label}-${tick.position}`" :style="{ '--tick': `${tick.position}%` }">{{ tick.label }}</span>
            </div>
          </div>

          <div class="timeline-tracks">
            <div v-if="activeMacro.steps.length" class="timeline-playlane"><i class="playhead" :style="playheadStyle()"><b></b></i></div>
            <div v-for="track in tracks" :key="track.id" class="track">
              <label>{{ track.label }}</label>
              <div class="track-lane">
                <button
                  v-for="item in timelineSteps.filter((step) => step.track === track.id)"
                  :key="item.index"
                  class="event"
                  :class="[`event-${item.tone}`, { selected: item.index === activeStepIndex }]"
                  :style="eventStyle(item.index)"
                  :title="`STEP ${item.index + 1} · ${item.detail}`"
                  @click="selectStep(item.index)"
                >{{ item.label }}</button>
              </div>
            </div>
          </div>

          <footer class="timeline-footer">
            <button class="status-link" style="padding: 0" @click="addStep('key_down')">＋ 按下</button>
            <button class="status-link" style="padding: 0" @click="addStep('key_up')">＋ 释放</button>
            <button class="status-link" style="padding: 0" @click="addStep('delay_ticks')">＋ 延迟</button>
            <button class="status-link" style="padding: 0" @click="addStep('pointer_step')">＋ 鼠标步进</button>
            <b>{{ activeMacro.steps.length }} 步 · {{ activeDelayTicks }} delay ticks</b>
          </footer>
        </div>

        <div v-else class="timeline-editor macro-empty-workspace">
          <div>
            <span class="empty-glyph">M</span>
            <h2>没有选中宏</h2>
            <p>这里不会自动选中或创建内容。请从左侧选择已有宏，或新建一个空白宏。</p>
            <button class="button primary" @click="createMacro">新建第一个宏</button>
          </div>
        </div>
      </section>

      <div class="status-strip">
        <div class="status-item"><i class="status-icon" :class="{ good: !validationIssues.length }">{{ validationIssues.length ? '!' : '✓' }}</i><p><b>{{ validationIssues.length ? 'Profile 宏结构需要处理' : 'Profile 宏结构可编辑' }}</b><small>{{ validationIssues[0] ?? `${totalSteps} 个步骤 · 顺序刻度已对齐` }}</small></p></div>
        <div class="status-item"><i class="status-icon">P</i><p><b>{{ macroDraftDirty ? '已写入 Profile 草稿' : '草稿与来源一致' }}</b><small>{{ localNotice }}</small></p></div>
        <button class="status-link" disabled title="固件宏执行器和编译器尚未实现">RuntimeTable 字节码待固件接入 →</button>
      </div>
    </section>

    <aside class="page-inspector">
      <div class="inspector-inner">
        <header class="inspector-head">
          <p class="tiny-label">{{ activeStep ? '当前宏步骤' : activeMacroId ? '当前宏' : 'MACRO_DEFS' }}</p>
          <h2 v-if="activeStep">{{ labelForStep(activeStep) }} <small>STEP {{ String((activeStepIndex ?? 0) + 1).padStart(2, '0') }}</small></h2>
          <h2 v-else-if="activeMacroId">{{ activeMacroId }} <small>PROFILE</small></h2>
          <h2 v-else>未选择 <small>EMPTY</small></h2>
          <p v-if="activeStep">{{ detailForStep(activeStep) }}</p>
          <p v-else-if="activeMacro">{{ activeMacro.steps.length }} 个步骤 · {{ activeDelayTicks }} delay ticks</p>
          <p v-else>选择一个宏后再编辑，不会预选内容</p>
        </header>
        <nav class="inspector-tabs" role="tablist" aria-label="宏检查器">
          <button role="tab" :aria-selected="inspectorTab === 'step'" :class="{ active: inspectorTab === 'step' }" :disabled="!activeStep" @click="inspectorTab = 'step'">步骤</button>
          <button role="tab" :aria-selected="inspectorTab === 'macro'" :class="{ active: inspectorTab === 'macro' }" :disabled="!activeMacro" @click="inspectorTab = 'macro'">宏</button>
          <button role="tab" :aria-selected="inspectorTab === 'test'" :class="{ active: inspectorTab === 'test' }" :disabled="!activeMacro" @click="inspectorTab = 'test'">检查</button>
        </nav>

        <div class="inspector-scroll">
          <template v-if="activeStep && inspectorTab === 'step'">
            <section class="inspector-section">
              <div class="section-title"><h3>步骤类型</h3><span class="chip blue">STEP {{ String((activeStepIndex ?? 0) + 1).padStart(2, '0') }}</span></div>
              <label class="field-label" for="macro-operation">Operation</label>
              <select id="macro-operation" class="macro-native-select" :value="activeStep.op" @change="changeOperation">
                <option v-for="option in operationOptions" :key="option.value" :value="option.value">{{ option.label }} · {{ option.value }}</option>
              </select>
              <div class="step-order-actions">
                <button class="button quiet" :disabled="activeStepIndex === 0" @click="moveStep(-1)">← 提前</button>
                <button class="button quiet" :disabled="activeStepIndex === activeMacro!.steps.length - 1" @click="moveStep(1)">延后 →</button>
              </div>
            </section>

            <section v-if="'usage' in activeStep" class="inspector-section">
              <label class="field-label" for="macro-usage">Usage</label>
              <input id="macro-usage" v-model.trim="activeUsage" class="macro-text-input" list="macro-common-usages" autocomplete="off" placeholder="keyboard.a" />
              <datalist id="macro-common-usages"><option v-for="usage in commonUsages" :key="usage" :value="usage" /></datalist>
              <div class="notice">keyboard 与 consumer 步骤直接保存完整 usage 字符串；固件白名单接入后再补能力筛选。</div>
            </section>

            <section v-else-if="activeStep.op === 'delay_ticks'" class="inspector-section">
              <label class="field-label" for="macro-delay">Delay ticks</label>
              <input id="macro-delay" v-model.number="activeDelay" class="macro-number-input" type="number" min="1" step="1" />
              <div class="notice">这里只写 Profile 的整数 ticks，不虚构毫秒换算；真实 tick 周期由固件 runtime contract 决定。</div>
            </section>

            <section v-else-if="activeStep.op === 'pointer_step'" class="inspector-section">
              <div class="section-title"><h3>鼠标步进</h3><span>整数增量</span></div>
              <div class="pointer-grid">
                <label><span>dx</span><input type="number" step="1" :value="pointerValue('dx')" @input="setPointerValue('dx', $event)" /></label>
                <label><span>dy</span><input type="number" step="1" :value="pointerValue('dy')" @input="setPointerValue('dy', $event)" /></label>
                <label><span>wheel</span><input type="number" step="1" :value="pointerValue('wheel')" @input="setPointerValue('wheel', $event)" /></label>
              </div>
            </section>

            <section v-else-if="activeStep.op === 'wait_release'" class="inspector-section">
              <label class="field-label" for="macro-source">Control source</label>
              <input id="macro-source" v-model.trim="activeWaitSource" class="macro-text-input" autocomplete="off" placeholder="key_000" />
              <div class="notice">等待指定 control source 释放后继续执行。</div>
            </section>

            <section v-else class="inspector-section"><div class="notice">stop 不带参数；执行器接入后会在此步骤终止当前宏。</div></section>
          </template>

          <template v-else-if="activeMacro && activeMacroId && inspectorTab === 'macro'">
            <section class="inspector-section">
              <div class="section-title"><h3>宏标识</h3><span>object key</span></div>
              <label class="field-label" for="macro-id">Macro ID</label>
              <input id="macro-id" v-model.trim="pendingMacroId" class="macro-text-input mono" autocomplete="off" />
              <div class="notice">重命名会改写 macro_defs 的 object key，并同步已有 macro_call behavior 引用。</div>
            </section>
            <section class="inspector-section">
              <div class="section-title"><h3>执行策略</h3><span>Profile source</span></div>
              <div class="macro-policy-line"><span><b>repeat</b><small>v1 仅支持有限、非递归宏</small></span><em>none</em></div>
              <button class="toggle-line" style="width: 100%; background: transparent; text-align: left" @click="toggleCancelOnRelease"><span><b>来源释放时取消</b><small>cancel_on_release</small></span><i class="switch" :class="{ on: activeMacro.cancel_on_release }"></i></button>
              <div class="macro-offset"><span>macro_call 引用</span><b>{{ activeReferenceCount }}</b><span>步骤</span><b>{{ activeMacro.steps.length }}</b><span>延迟总计</span><b>{{ activeDelayTicks }} ticks</b></div>
            </section>
          </template>

          <template v-else-if="activeMacro && inspectorTab === 'test'">
            <section class="inspector-section macro-test">
              <div class="section-title"><h3>静态检查</h3><span class="chip" :class="validationIssues.length ? 'coral' : 'green'"><i></i>{{ validationIssues.length ? `${validationIssues.length} 项` : '通过' }}</span></div>
              <p>{{ activeMacro.steps.length }} 个步骤 · {{ activeDelayTicks }} delay ticks · {{ activeReferenceCount }} 个引用</p>
            </section>
            <section class="inspector-section"><div class="notice">步骤播放只是顺序预览，不发送按键、不读取设备，也不会向系统注入输入。宏字节码仍等待固件与编译器实现。</div></section>
          </template>

          <section v-else class="inspector-section"><div class="notice">当前没有选中宏或步骤。主界面保持空选择，直到你主动选择内容。</div></section>
        </div>

        <div class="inspector-actions">
          <template v-if="activeStep && inspectorTab === 'step'">
            <button class="button danger" @click="deleteStep">删除步骤</button>
            <button class="button primary" @click="addStep('delay_ticks')">在后面加延迟</button>
          </template>
          <template v-else-if="activeMacro && inspectorTab === 'macro'">
            <button class="button danger" :disabled="activeReferenceCount > 0" :title="activeReferenceCount > 0 ? '该宏仍被 macro_call behavior 引用' : ''" @click="deleteMacro">{{ deleteArmed ? '确认删除宏' : '删除宏' }}</button>
            <button class="button primary" :disabled="pendingMacroId === activeMacroId" @click="renameMacro">应用重命名</button>
          </template>
          <template v-else>
            <button class="button quiet" disabled>无可用操作</button>
            <button class="button primary" :disabled="!!activeMacro" @click="createMacro">新建宏</button>
          </template>
        </div>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.macro-empty-list {
  display: grid;
  gap: 7px;
  margin: 8px;
  padding: 14px 10px;
  color: var(--muted);
  border: 1px dashed var(--line-strong);
  border-radius: 9px;
}

.macro-empty-list b { color: var(--ink-soft); font-size: 9px; }
.macro-empty-list span { font-size: 8px; line-height: 1.55; }
.macro-limits small { display: block; margin-top: 10px; color: var(--muted-2); font-size: 7px; line-height: 1.45; }
.macro-list { overflow-y: auto; }

.macro-empty-workspace {
  display: grid;
  place-items: center;
  padding: 24px;
}

.macro-empty-workspace > div { max-width: 290px; display: grid; justify-items: start; gap: 10px; }
.macro-empty-workspace h2 { font: 600 18px/1 var(--font-display); }
.macro-empty-workspace p { color: var(--muted); font-size: 9px; line-height: 1.65; }
.empty-glyph { width: 34px; height: 34px; display: grid; place-items: center; border-radius: 10px; color: var(--accent-strong); background: var(--accent-soft); font: 700 13px/1 var(--font-utility); }

.step-ruler span:first-child,
.step-ruler span:last-child { transform: translateX(-50%); }

.timeline-tracks::before {
  background: repeating-linear-gradient(
    90deg,
    transparent 0 calc(var(--step-grid) - 1px),
    rgba(203, 210, 220, .45) calc(var(--step-grid) - 1px) var(--step-grid)
  );
}

.macro-native-select,
.macro-text-input,
.macro-number-input,
.pointer-grid input {
  width: 100%;
  height: 38px;
  padding: 0 11px;
  color: var(--ink-soft);
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  outline: none;
  background: white;
  font-size: 9px;
}

.macro-native-select:focus,
.macro-text-input:focus,
.macro-number-input:focus,
.pointer-grid input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }

.step-order-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 10px; }
.step-order-actions .button { min-width: 0; }

.pointer-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 7px; }
.pointer-grid label { display: grid; gap: 6px; color: var(--muted); font: 7px/1 var(--font-utility); }
.pointer-grid input { height: 34px; padding: 0 7px; }

.macro-policy-line { min-height: 48px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--line); }
.macro-policy-line span { display: grid; gap: 3px; }
.macro-policy-line b { font-size: 9px; }
.macro-policy-line small { color: var(--muted); font-size: 7px; }
.macro-policy-line em { color: var(--accent-strong); font: 8px/1 var(--font-utility); font-style: normal; }

@media (max-width: 1080px) {
  .timeline-footer { gap: 10px; }
  .timeline-footer button:nth-of-type(4) { display: none; }
}
</style>
