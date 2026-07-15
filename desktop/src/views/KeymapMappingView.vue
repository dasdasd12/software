<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { onBeforeRouteLeave } from "vue-router";
import KeyboardBoard from "../components/keyboard/KeyboardBoard.vue";
import { useUnsavedChangesGuard } from "../composables/useUnsavedChangesGuard";
import { getKeyboardKey } from "../domain/keyboard/layout";
import {
  BINDING_CATEGORY_LABELS,
  bindingCategory,
  bindingDescription,
  bindingLabel,
} from "../domain/profile/bindings";
import type {
  AkeyControlAssignment,
  AkeyTriggerMode,
  ProfileDocument,
} from "../domain/profile/types";
import { useDeviceStore } from "../stores/device";
import { useEditorStore } from "../stores/editor";
import { useProfileStore } from "../stores/profile";

type InspectorTab = "mapping" | "trigger" | "advanced";
type BindingCategory = keyof typeof BINDING_CATEGORY_LABELS;

const profile = useProfileStore();
const device = useDeviceStore();
const editor = useEditorStore();
const inspectorTab = ref<InspectorTab>("mapping");
const activeGroup = ref("");
const selectedCategory = ref<BindingCategory>("keyboard");
const pendingBinding = ref("keyboard.a");
const pendingBindingTouched = ref(false);

const commonBindings = [
  "no_op",
  "keyboard.escape",
  "keyboard.tab",
  "keyboard.enter",
  "keyboard.space",
  "keyboard.backspace",
  "keyboard.left",
  "keyboard.right",
  "keyboard.up",
  "keyboard.down",
  "consumer.volume_increment",
  "consumer.volume_decrement",
  "consumer.mute",
  "mouse.wheel_up",
  "mouse.wheel_down",
];

const hasSelection = computed(() => editor.selectionCount > 0);
const primaryKey = computed(() => getKeyboardKey(editor.primaryControlId));
const currentBinding = computed(() =>
  hasSelection.value ? profile.bindingFor(editor.primaryControlId) : "",
);
const selectedBindings = computed(() =>
  Array.from(new Set(editor.selectedControlIds.map((id) => profile.bindingFor(id)))),
);
const mixedSelection = computed(() => selectedBindings.value.length > 1);
const selectedDirtyCount = computed(() =>
  editor.selectedControlIds.filter((id) => profile.dirtyControlIds.includes(id)).length,
);
const selectedIsDirty = computed(() => selectedDirtyCount.value > 0);
const hardwareBaseId = computed(() => {
  if (!hasSelection.value) return null;
  const [base] = editor.primaryControlId.split(".");
  return base === "fiveway_000" || base === "enc_000" ? base : null;
});
const hardwareEvents = computed(() => {
  if (hardwareBaseId.value === "fiveway_000") {
    return ["up", "down", "left", "right", "press", "cw_step", "ccw_step"];
  }
  if (hardwareBaseId.value === "enc_000") return ["ccw_step", "press", "cw_step"];
  return [];
});
const activeHardwareEvent = computed(() =>
  hasSelection.value ? editor.primaryControlId.split(".")[1] ?? "press" : "",
);
const hardwareEventLabels: Record<string, string> = {
  up: "上",
  down: "下",
  left: "左",
  right: "右",
  press: "按压",
  cw_step: "顺时针",
  ccw_step: "逆时针",
};

const controlTitle = computed(() => {
  if (!hasSelection.value) return "未选择控件";
  if (primaryKey.value) return primaryKey.value.label;
  if (editor.primaryControlId.startsWith("fiveway_000")) return "五向摇杆";
  if (editor.primaryControlId.startsWith("enc_000")) return "EC11 编码器";
  return "未命名控件";
});

const controlMeta = computed(() => {
  if (!hasSelection.value) return "选择键帽、五向摇杆或旋钮后，再编辑它的配置";
  if (primaryKey.value) {
    const half = primaryKey.value.side === "left" ? "左半区" : "右半区";
    return `${half} · 磁轴键 · ${profile.activeScopeId} 层`;
  }
  const event = editor.primaryControlId.split(".")[1] ?? "press";
  return `硬件控件 · ${event} 事件 · ${profile.activeScopeId} 层`;
});

const allBindings = computed(() => {
  const source = Object.values(profile.activeScope?.bindings ?? {});
  return Array.from(new Set([...source, ...commonBindings])).sort((a, b) => {
    const categoryDifference = bindingCategory(a).localeCompare(bindingCategory(b));
    return categoryDifference || a.localeCompare(b);
  });
});

const visibleBindings = computed(() =>
  allBindings.value.filter((binding) => bindingCategory(binding) === selectedCategory.value),
);

const canApply = computed(() =>
  hasSelection.value
  && Boolean(pendingBinding.value)
  && editor.selectedControlIds.some((id) => profile.bindingFor(id) !== pendingBinding.value),
);
const mappingBufferDirty = computed(() => pendingBindingTouched.value && canApply.value);

const compileTitle = computed(() => {
  if (profile.compileState === "compiling") return "正在编译 Profile";
  if (profile.compileState === "valid") return "Profile 可编译";
  if (profile.compileState === "error") return "Profile 编译失败";
  return profile.isDirty ? "Profile 有本地更改" : "Profile 已载入";
});

const targetMeta = computed(() => {
  if (!device.connected) return "尚未连接设备，只保存本地草稿";
  return `${device.port ?? "USB"} · ${device.slotCount} 个可用 Slot`;
});

interface TriggerEditor {
  mode: AkeyTriggerMode;
  press: number;
  release: number;
  reset: number;
  releaseDelta: number;
  pressDelta: number;
  deadzone: number;
  retriggerBeforeReset: boolean;
}

function isAkeyAssignment(value: unknown): value is AkeyControlAssignment {
  if (typeof value !== "object" || value === null) return false;
  const assignment = value as Partial<AkeyControlAssignment>;
  return assignment.type === "akey"
    && Array.isArray(assignment.controls)
    && assignment.controls.every((control) => typeof control === "string")
    && (assignment.mode === "normal" || assignment.mode === "rapid_trigger" || assignment.mode === "disabled")
    && typeof assignment.params === "object"
    && assignment.params !== null
    && !Array.isArray(assignment.params);
}

function resolvesControl(assignment: AkeyControlAssignment, controlId: string): boolean {
  return assignment.controls.includes("@main_keys") || assignment.controls.includes(controlId);
}

function integerParam(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isInteger(value) ? value : fallback;
}

function booleanParam(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function resolveTrigger(document: ProfileDocument, controlId: string): TriggerEditor {
  const akey = document.defaults.triggers?.akey;
  const common = akey?.common;
  const normal = akey?.normal;
  const rapid = akey?.rapid_trigger;
  const defaultPress = integerParam(normal?.press_threshold_norm_i16, 400);
  const defaultRelease = integerParam(normal?.release_threshold_norm_i16, 350);
  const defaultReset = integerParam(rapid?.reset_threshold_norm_i16, 350);
  const defaultReleaseDelta = integerParam(rapid?.release_delta_norm_i16, 100);
  const defaultPressDelta = integerParam(rapid?.press_delta_norm_i16, 100);
  const defaultDeadzone = integerParam(common?.deadzone_norm_i16, 20);
  const defaultRetrigger = booleanParam(rapid?.retrigger_before_reset, false);
  const base: TriggerEditor = {
    mode: "rapid_trigger",
    press: defaultPress / 10,
    release: defaultRelease / 10,
    reset: defaultReset / 10,
    releaseDelta: defaultReleaseDelta / 10,
    pressDelta: defaultPressDelta / 10,
    deadzone: defaultDeadzone / 10,
    retriggerBeforeReset: defaultRetrigger,
  };

  for (const candidate of document.control_assignments) {
    if (!isAkeyAssignment(candidate) || !resolvesControl(candidate, controlId)) continue;
    const params = candidate.params;
    base.mode = candidate.mode;
    base.press = integerParam(params.press_threshold_norm_i16, defaultPress) / 10;
    base.release = integerParam(params.release_threshold_norm_i16, defaultRelease) / 10;
    base.reset = integerParam(params.reset_threshold_norm_i16, defaultReset) / 10;
    base.releaseDelta = integerParam(params.release_delta_norm_i16, defaultReleaseDelta) / 10;
    base.pressDelta = integerParam(params.press_delta_norm_i16, defaultPressDelta) / 10;
    base.deadzone = integerParam(params.deadzone_norm_i16, defaultDeadzone) / 10;
    base.retriggerBeforeReset = booleanParam(params.retrigger_before_reset, defaultRetrigger);
  }
  return base;
}

const selectedKeyIds = computed(() =>
  editor.selectedControlIds.filter((controlId) => controlId.startsWith("key_")),
);
const resolvedTrigger = computed<TriggerEditor>(() =>
  resolveTrigger(profile.draftDocument, editor.primaryControlId),
);
const selectedTriggerValues = computed(() =>
  selectedKeyIds.value.map((controlId) => resolveTrigger(profile.draftDocument, controlId)),
);
const triggerMixed = computed(() =>
  new Set(selectedTriggerValues.value.map((value) => JSON.stringify(value))).size > 1,
);
const triggerPreview = ref<TriggerEditor>({ ...resolvedTrigger.value });
const triggerPreviewDirty = computed(() =>
  JSON.stringify(triggerPreview.value) !== JSON.stringify(resolvedTrigger.value),
);
const triggerValidationMessage = computed(() => {
  if (triggerPreview.value.mode === "normal" && triggerPreview.value.release >= triggerPreview.value.press) {
    return "固定释放点必须浅于初始触发点";
  }
  const values = [
    triggerPreview.value.press,
    triggerPreview.value.release,
    triggerPreview.value.reset,
    triggerPreview.value.releaseDelta,
    triggerPreview.value.pressDelta,
    triggerPreview.value.deadzone,
  ];
  if (values.some((value) => value < 0 || value > 100 || !Number.isFinite(value))) {
    return "触发参数必须位于 0–100%";
  }
  if (triggerPreview.value.mode === "rapid_trigger"
    && (triggerPreview.value.releaseDelta < 0.1 || triggerPreview.value.pressDelta < 0.1)) {
    return "RT 灵敏度至少为 0.1%";
  }
  return "";
});
function lastMatchingAssignment(controlId: string): AkeyControlAssignment | null {
  let match: AkeyControlAssignment | null = null;
  for (const candidate of profile.draftDocument.control_assignments) {
    if (isAkeyAssignment(candidate) && resolvesControl(candidate, controlId)) match = candidate;
  }
  return match;
}
const effectiveTriggerSource = computed(() => {
  if (triggerMixed.value) return "混合参数";
  const assignment = lastMatchingAssignment(editor.primaryControlId);
  return assignment?.controls.length === 1 && assignment.controls[0] === editor.primaryControlId
    ? "单键覆盖"
    : "继承 Profile";
});
const hasExactTriggerOverride = computed(() => selectedKeyIds.value.some((controlId) =>
  profile.draftDocument.control_assignments.some((candidate) =>
    isAkeyAssignment(candidate)
    && candidate.controls.length === 1
    && candidate.controls[0] === controlId,
  ),
));
const canApplyCurrent = computed(() => {
  if (inspectorTab.value === "mapping") return canApply.value;
  if (inspectorTab.value === "trigger") {
    return selectedKeyIds.value.length > 0
      && (triggerPreviewDirty.value || triggerMixed.value)
      && !triggerValidationMessage.value;
  }
  return false;
});
const primaryActionLabel = computed(() => {
  if (!hasSelection.value) return "选择控件后可应用";
  if (inspectorTab.value === "trigger") {
    return selectedKeyIds.value.length > 1
      ? `保存到 ${selectedKeyIds.value.length} 个键`
      : "保存单键触发参数";
  }
  if (inspectorTab.value === "advanced") return "请在高级行为页配置";
  return editor.selectionCount > 1 ? `统一应用到 ${editor.selectionCount} 个控件` : "应用到所选键";
});
const secondaryActionLabel = computed(() => {
  if (inspectorTab.value === "mapping") return mappingBufferDirty.value ? "放弃映射选择" : "恢复来源值";
  if (inspectorTab.value !== "trigger") return "恢复来源值";
  return triggerPreviewDirty.value ? "放弃参数修改" : "移除单键覆盖";
});
const secondaryActionDisabled = computed(() => {
  if (!hasSelection.value) return true;
  if (inspectorTab.value === "mapping") return !mappingBufferDirty.value && !selectedIsDirty.value;
  if (inspectorTab.value !== "trigger") return true;
  return !triggerPreviewDirty.value && !hasExactTriggerOverride.value;
});

watch(
  [() => editor.primaryControlId, currentBinding],
  () => {
    if (!hasSelection.value) {
      inspectorTab.value = "mapping";
      return;
    }
    pendingBinding.value = currentBinding.value;
    selectedCategory.value = bindingCategory(currentBinding.value);
    pendingBindingTouched.value = false;
  },
  { immediate: true },
);

watch(resolvedTrigger, (value) => {
  triggerPreview.value = { ...value };
});

watch(primaryKey, (key) => {
  if (!key && inspectorTab.value !== "mapping") inspectorTab.value = "mapping";
});

function confirmDiscardInspectorBuffer(): boolean {
  const hasMappingBuffer = inspectorTab.value === "mapping" && mappingBufferDirty.value;
  const hasTriggerBuffer = inspectorTab.value === "trigger" && triggerPreviewDirty.value;
  if (!hasMappingBuffer && !hasTriggerBuffer) return true;
  const message = hasMappingBuffer ? "放弃尚未应用的映射选择？" : "放弃尚未保存的触发参数修改？";
  const accepted = window.confirm(message);
  if (accepted && hasMappingBuffer) {
    pendingBinding.value = currentBinding.value;
    selectedCategory.value = bindingCategory(currentBinding.value);
    pendingBindingTouched.value = false;
  }
  if (accepted && hasTriggerBuffer) triggerPreview.value = { ...resolvedTrigger.value };
  return accepted;
}

onBeforeRouteLeave(() => confirmDiscardInspectorBuffer());
useUnsavedChangesGuard(
  () => mappingBufferDirty.value || triggerPreviewDirty.value,
  "键位编辑器中还有尚未应用的映射或触发参数。",
);

function setInspectorTab(next: InspectorTab): void {
  if (next === inspectorTab.value) return;
  if (!confirmDiscardInspectorBuffer()) return;
  inspectorTab.value = next;
}

function chooseCategory(category: BindingCategory): void {
  selectedCategory.value = category;
  const currentMatches = bindingCategory(pendingBinding.value) === category;
  if (!currentMatches) {
    pendingBinding.value = allBindings.value.find((binding) => bindingCategory(binding) === category) ?? "";
    pendingBindingTouched.value = pendingBinding.value !== currentBinding.value;
  }
}

function selectControl(payload: { id: string; additive: boolean; range: boolean }): void {
  if (!confirmDiscardInspectorBuffer()) return;
  activeGroup.value = "";
  editor.selectControl(payload.id, payload.additive, payload.range);
}

function chooseGroup(group: string): void {
  if (!confirmDiscardInspectorBuffer()) return;
  activeGroup.value = group;
  editor.selectGroup(group);
}

function clearSelection(): void {
  if (!confirmDiscardInspectorBuffer()) return;
  activeGroup.value = "";
  editor.clearSelection();
}

function applyBinding(): void {
  if (!canApply.value) return;
  profile.setBinding(editor.selectedControlIds, pendingBinding.value);
  pendingBindingTouched.value = false;
}

function restoreBinding(): void {
  if (!hasSelection.value) return;
  profile.restoreBindings(editor.selectedControlIds);
  pendingBinding.value = profile.bindingFor(editor.primaryControlId);
  selectedCategory.value = bindingCategory(pendingBinding.value);
}

function setTriggerMode(mode: TriggerEditor["mode"]): void {
  triggerPreview.value.mode = mode;
}

function saveTriggerOverrides(): void {
  if (!canApplyCurrent.value || inspectorTab.value !== "trigger") return;
  const keys = [...selectedKeyIds.value];
  const selectedSet = new Set(keys);
  const previousExact = new Map<string, AkeyControlAssignment>();
  for (const candidate of profile.draftDocument.control_assignments) {
    if (isAkeyAssignment(candidate)
      && candidate.controls.length === 1
      && selectedSet.has(candidate.controls[0])) {
      previousExact.set(candidate.controls[0], candidate);
    }
  }
  profile.draftDocument.control_assignments = profile.draftDocument.control_assignments.filter((candidate) =>
    !(
      isAkeyAssignment(candidate)
      && candidate.controls.length === 1
      && selectedSet.has(candidate.controls[0])
    ),
  );

  const knownParamKeys = new Set([
    "defaults", "press_threshold_norm_i16", "release_threshold_norm_i16",
    "reset_threshold_norm_i16", "release_delta_norm_i16", "press_delta_norm_i16",
    "deadzone_norm_i16", "retrigger_before_reset",
  ]);
  for (const controlId of keys) {
    const previous = previousExact.get(controlId);
    const extensionParams = Object.fromEntries(
      Object.entries(previous?.params ?? {}).filter(([key]) => !knownParamKeys.has(key)),
    );
    const params: AkeyControlAssignment["params"] = { ...extensionParams, defaults: true };
    if (triggerPreview.value.mode === "normal") {
      params.deadzone_norm_i16 = Math.round(triggerPreview.value.deadzone * 10);
      params.press_threshold_norm_i16 = Math.round(triggerPreview.value.press * 10);
      params.release_threshold_norm_i16 = Math.round(triggerPreview.value.release * 10);
    } else if (triggerPreview.value.mode === "rapid_trigger") {
      params.deadzone_norm_i16 = Math.round(triggerPreview.value.deadzone * 10);
      params.press_threshold_norm_i16 = Math.round(triggerPreview.value.press * 10);
      params.reset_threshold_norm_i16 = Math.round(triggerPreview.value.reset * 10);
      params.release_delta_norm_i16 = Math.round(triggerPreview.value.releaseDelta * 10);
      params.press_delta_norm_i16 = Math.round(triggerPreview.value.pressDelta * 10);
      params.retrigger_before_reset = triggerPreview.value.retriggerBeforeReset;
    }
    profile.draftDocument.control_assignments.push({
      ...(previous ?? {}),
      controls: [controlId],
      type: "akey",
      mode: triggerPreview.value.mode,
      params,
    });
  }
  profile.markDraftChanged(`已保存 ${keys.length} 个按键的触发覆盖`);
  triggerPreview.value = { ...resolveTrigger(profile.draftDocument, editor.primaryControlId) };
}

function removeTriggerOverrides(): void {
  const selectedSet = new Set(selectedKeyIds.value);
  profile.draftDocument.control_assignments = profile.draftDocument.control_assignments.filter((candidate) =>
    !(
      isAkeyAssignment(candidate)
      && candidate.controls.length === 1
      && selectedSet.has(candidate.controls[0])
    ),
  );
  profile.markDraftChanged("已移除所选按键的单键触发覆盖");
  triggerPreview.value = { ...resolveTrigger(profile.draftDocument, editor.primaryControlId) };
}

function runSecondaryAction(): void {
  if (inspectorTab.value === "mapping") {
    if (mappingBufferDirty.value) {
      pendingBinding.value = currentBinding.value;
      selectedCategory.value = bindingCategory(currentBinding.value);
      pendingBindingTouched.value = false;
    } else {
      restoreBinding();
    }
    return;
  }
  if (inspectorTab.value !== "trigger") {
    restoreBinding();
    return;
  }
  if (triggerPreviewDirty.value) {
    triggerPreview.value = { ...resolvedTrigger.value };
  } else {
    removeTriggerOverrides();
  }
}

function runPrimaryAction(): void {
  if (inspectorTab.value === "mapping") applyBinding();
  else if (inspectorTab.value === "trigger") saveTriggerOverrides();
}

function chooseHardwareEvent(event: string): void {
  if (!hardwareBaseId.value) return;
  editor.selectControl(`${hardwareBaseId.value}.${event}`);
}
</script>

<template>
  <div class="page-shell">
    <section class="page-main keymap-main">
      <header class="page-heading">
        <div>
          <p class="page-kicker">PROFILE / INPUT MAP</p>
          <h1>键位与触发</h1>
          <p class="lede">在真实配列上选择控件，再编辑输出、触发方式与行为。</p>
        </div>
        <div class="heading-actions">
          <div class="scope-control">
            <span>绑定域</span>
            <div class="segmented compact" aria-label="绑定域">
              <button class="active">Base</button>
              <button disabled title="Profile 中尚未定义 Fn scope">Fn</button>
              <button disabled aria-label="添加绑定域">＋</button>
            </div>
          </div>
        </div>
      </header>

      <div class="keyboard-intro">
        <span><small>77 键 · 分体磁轴 · CAD 实际坐标</small><b>AK Ergo 77</b></span>
        <span class="chip blue">输出视图</span>
      </div>

      <section class="keyboard-surface">
        <KeyboardBoard
          :selected-ids="editor.selectedControlIds"
          :dirty-ids="profile.dirtyControlIds"
          @select="selectControl"
          @clear="clearSelection"
        />
        <div class="keyboard-toolbar">
          <span><kbd>Click</kbd>单选　<kbd>Shift</kbd>连续选择　<kbd>Ctrl</kbd>多选　点击空白处清除</span>
          <div class="key-groups">
            <button
              v-for="group in ['主键', 'WASD', '左半区', '右半区']"
              :key="group"
              :class="{ active: activeGroup === group }"
              @click="chooseGroup(group)"
            >{{ group }}</button>
          </div>
          <span class="selection-count">{{ hasSelection ? `${editor.selectionCount} 个控件` : "未选择" }}</span>
        </div>
      </section>

      <div class="status-strip" aria-live="polite">
        <div class="status-item">
          <i class="status-icon" :class="{ good: profile.compileState === 'valid' }">
            {{ profile.compileState === 'valid' ? '✓' : profile.compileState === 'error' ? '!' : 'P' }}
          </i>
          <p><b>{{ compileTitle }}</b><small>{{ profile.compileMessage }}</small></p>
        </div>
        <div class="status-item">
          <i class="status-icon">{{ String(device.targetUserSlot).padStart(2, '0') }}</i>
          <p><b>目标 Slot {{ device.targetUserSlot }}</b><small>{{ targetMeta }}</small></p>
        </div>
        <button class="status-link" :disabled="profile.compileState === 'compiling'" @click="profile.validateDraft">
          验证草稿 →
        </button>
      </div>
    </section>

    <aside class="page-inspector">
      <div class="inspector-inner">
        <header class="inspector-head">
          <p class="tiny-label">{{ hasSelection ? "当前控件" : "配置检查器" }}</p>
          <h2>{{ controlTitle }} <small v-if="hasSelection">{{ editor.primaryControlId }}</small></h2>
          <p>{{ controlMeta }}</p>
          <div v-if="hardwareEvents.length" class="hardware-event-picker" aria-label="硬件控件事件">
            <button
              v-for="event in hardwareEvents"
              :key="event"
              :class="{ active: activeHardwareEvent === event }"
              @click="chooseHardwareEvent(event)"
            >{{ hardwareEventLabels[event] }}</button>
          </div>
        </header>
        <nav v-if="hasSelection" class="inspector-tabs" role="tablist" aria-label="控件设置类型">
          <button role="tab" :aria-selected="inspectorTab === 'mapping'" :class="{ active: inspectorTab === 'mapping' }" @click="setInspectorTab('mapping')">映射</button>
          <button role="tab" :aria-selected="inspectorTab === 'trigger'" :class="{ active: inspectorTab === 'trigger' }" :disabled="!primaryKey" title="仅磁轴键支持触发参数" @click="setInspectorTab('trigger')">触发</button>
          <button role="tab" :aria-selected="inspectorTab === 'advanced'" :class="{ active: inspectorTab === 'advanced' }" :disabled="!primaryKey" title="仅磁轴键支持高级行为" @click="setInspectorTab('advanced')">高级</button>
        </nav>

        <div class="inspector-scroll">
          <div v-if="!hasSelection" class="selection-empty" role="status">
            <div class="selection-empty__key" aria-hidden="true"><span></span></div>
            <p class="tiny-label">NO CONTROL SELECTED</p>
            <h3>先看配列，准备好再选</h3>
            <p>点击左侧任意键帽、五向摇杆或旋钮，右侧会显示对应映射。切换页面与 Profile 不需要先选择按键。</p>
          </div>

          <template v-else-if="inspectorTab === 'mapping'">
            <section class="inspector-section">
              <div class="section-title"><h3>输出类型</h3><span>{{ profile.activeScopeId }} 层</span></div>
              <div class="category-grid">
                <button
                  v-for="(label, category) in BINDING_CATEGORY_LABELS"
                  :key="category"
                  :class="{ active: selectedCategory === category }"
                  @click="chooseCategory(category)"
                >{{ label }}</button>
              </div>
            </section>
            <section class="inspector-section">
              <div class="section-title">
                <h3>当前映射</h3>
                <span class="chip" :class="mixedSelection ? 'amber' : selectedIsDirty ? 'coral' : 'blue'">
                  {{ mixedSelection ? '混合映射' : selectedIsDirty ? '本地覆盖' : '来自 Profile' }}
                </span>
              </div>
              <div v-if="mixedSelection" class="notice warning">
                所选 {{ editor.selectionCount }} 个控件包含 {{ selectedBindings.length }} 种映射。下方显示主选控件的值；应用会把全部所选控件统一覆盖。
              </div>
              <label class="field-label" for="binding-select">{{ BINDING_CATEGORY_LABELS[selectedCategory] }}动作</label>
              <div v-if="visibleBindings.length" class="select-field binding-select-field">
                <i class="field-icon">{{ bindingLabel(pendingBinding).slice(-1).toUpperCase() }}</i>
                <span>
                  <strong>{{ bindingLabel(pendingBinding) }}</strong>
                  <small>{{ pendingBinding }}</small>
                </span>
                <select id="binding-select" v-model="pendingBinding" aria-label="选择输出动作" @change="pendingBindingTouched = true">
                  <option v-for="binding in visibleBindings" :key="binding" :value="binding">
                    {{ bindingLabel(binding) }} · {{ binding }}
                  </option>
                </select>
                <i class="field-chevron">⌄</i>
              </div>
              <div v-else class="notice">这个类型在当前 Profile 中还没有可用动作。</div>
            </section>
            <section class="inspector-section">
              <div class="section-title"><h3>解析预览</h3><span>已解析</span></div>
              <div class="binding-line">
                <span class="key-token selected">{{ editor.selectionCount > 1 ? `${editor.selectionCount} 个控件` : controlTitle }}</span><i>→</i>
                <p><b>{{ pendingBinding || '—' }}</b><small>{{ pendingBinding ? bindingDescription(pendingBinding) : '等待选择动作' }}</small></p>
              </div>
            </section>
            <section v-if="primaryKey" class="inspector-section">
              <button class="inline-link" @click="setInspectorTab('advanced')">转为高级行为 <span>Tap-Hold / DKS →</span></button>
            </section>
          </template>

          <template v-else-if="inspectorTab === 'trigger'">
            <section class="inspector-section">
              <div class="section-title"><h3>触发模式</h3><span class="chip" :class="effectiveTriggerSource === '混合参数' ? 'amber' : effectiveTriggerSource === '单键覆盖' ? 'coral' : 'blue'">{{ effectiveTriggerSource }}</span></div>
              <div class="mode-list">
                <div class="mode-row clickable" :class="{ active: triggerPreview.mode === 'normal' }" role="button" tabindex="0" @click="setTriggerMode('normal')" @keydown.enter="setTriggerMode('normal')" @keydown.space.prevent="setTriggerMode('normal')"><span><b>Normal</b><small>固定触发 / 释放点</small></span><i class="radio" :class="{ active: triggerPreview.mode === 'normal' }"></i></div>
                <div class="mode-row clickable" :class="{ active: triggerPreview.mode === 'rapid_trigger' }" role="button" tabindex="0" @click="setTriggerMode('rapid_trigger')" @keydown.enter="setTriggerMode('rapid_trigger')" @keydown.space.prevent="setTriggerMode('rapid_trigger')"><span><b>Rapid Trigger</b><small>动态触发 / 动态释放</small></span><i class="radio" :class="{ active: triggerPreview.mode === 'rapid_trigger' }"></i></div>
                <div class="mode-row clickable" :class="{ active: triggerPreview.mode === 'disabled' }" role="button" tabindex="0" @click="setTriggerMode('disabled')" @keydown.enter="setTriggerMode('disabled')" @keydown.space.prevent="setTriggerMode('disabled')"><span><b>Disabled</b><small>输入侧不生成触发信号</small></span><i class="radio" :class="{ active: triggerPreview.mode === 'disabled' }"></i></div>
                <div class="mode-row disabled-option" title="当前编译器尚不支持 analog"><span><b>Analog</b><small>运行时待实现</small></span><i class="radio"></i></div>
              </div>
            </section>
            <section v-if="triggerMixed" class="inspector-section"><div class="notice warning">所选按键包含不同触发参数。当前显示主选键的值；保存会把这组值统一写入 {{ selectedKeyIds.length }} 个键。</div></section>
            <section v-if="triggerPreview.mode !== 'disabled'" class="inspector-section">
              <div class="section-title"><h3>行程响应</h3><span>{{ triggerPreviewDirty ? '未保存' : '有效值' }}</span></div>
              <div class="graph-panel">
                <span class="graph-label tl">PRESSED</span><span class="graph-label tr">{{ triggerPreview.press }}%</span>
                <span class="graph-label bl">RELEASED</span>
                <svg viewBox="0 0 300 110" fill="none" aria-hidden="true">
                  <path d="M12 91 C45 91 58 84 78 80 S112 26 145 26 S174 74 198 74 S232 38 288 38" stroke="#a5adb9" stroke-width="2"/>
                  <path d="M12 91 C45 91 58 84 78 80 S112 26 145 26" stroke="#4e68f6" stroke-width="3"/>
                  <circle cx="145" cy="26" r="5" fill="white" stroke="#ff715b" stroke-width="2"/>
                </svg>
              </div>
              <label class="native-parameter"><span>初始触发点</span><b>{{ triggerPreview.press.toFixed(1) }}%</b><input v-model.number="triggerPreview.press" type="range" min="0" max="100" step="0.1" /></label>
              <label class="native-parameter"><span>输入死区</span><b>{{ triggerPreview.deadzone.toFixed(1) }}%</b><input v-model.number="triggerPreview.deadzone" type="range" min="0" max="100" step="0.1" /></label>
              <label v-if="triggerPreview.mode === 'normal'" class="native-parameter"><span>固定释放点</span><b>{{ triggerPreview.release.toFixed(1) }}%</b><input v-model.number="triggerPreview.release" type="range" min="0" max="100" step="0.1" /></label>
              <template v-else>
                <label class="native-parameter"><span>RT 复位点</span><b>{{ triggerPreview.reset.toFixed(1) }}%</b><input v-model.number="triggerPreview.reset" type="range" min="0" max="100" step="0.1" /></label>
                <label class="native-parameter"><span>释放灵敏度</span><b>{{ triggerPreview.releaseDelta.toFixed(1) }}%</b><input v-model.number="triggerPreview.releaseDelta" type="range" min="0.1" max="100" step="0.1" /></label>
                <div class="toggle-line" role="button" tabindex="0" @click="triggerPreview.retriggerBeforeReset = !triggerPreview.retriggerBeforeReset" @keydown.enter="triggerPreview.retriggerBeforeReset = !triggerPreview.retriggerBeforeReset" @keydown.space.prevent="triggerPreview.retriggerBeforeReset = !triggerPreview.retriggerBeforeReset">
                  <span><b>完全复位前允许再触发</b><small>保存到 source；当前单键 RuntimeTable 暂不执行</small></span><i class="switch" :class="{ on: triggerPreview.retriggerBeforeReset }"></i>
                </div>
                <label v-if="triggerPreview.retriggerBeforeReset" class="native-parameter"><span>再触发灵敏度</span><b>{{ triggerPreview.pressDelta.toFixed(1) }}%</b><input v-model.number="triggerPreview.pressDelta" type="range" min="0.1" max="100" step="0.1" /></label>
              </template>
            </section>
            <section v-else class="inspector-section"><div class="notice warning">Disabled 会保留 Control Data，但所选按键不再生成触发信号。键位映射不会被删除。</div></section>
            <section v-if="triggerValidationMessage" class="inspector-section"><div class="notice warning">{{ triggerValidationMessage }}</div></section>
            <section v-else class="inspector-section"><div class="notice">保存后会在 control_assignments 末尾写入所选键的整数单键覆盖；移除覆盖即可重新继承 Profile 默认值。</div></section>
          </template>

          <template v-else>
            <section class="inspector-section">
              <div class="section-title"><h3>高级行为</h3><span>下一条数据链路</span></div>
              <div class="advanced-choice"><b>Tap-Hold</b><small>轻触与长按分别输出动作</small></div>
              <div class="advanced-choice"><b>DKS</b><small>按行程区间触发多个动作</small></div>
              <div class="advanced-choice"><b>Overlay</b><small>按条件激活附加绑定域</small></div>
            </section>
            <section class="inspector-section"><div class="notice">这些行为会严格按现有 Profile schema 建模，不会把预览数据写成另一套格式。</div></section>
          </template>
        </div>

        <div class="inspector-actions">
          <button class="button quiet" :disabled="secondaryActionDisabled" @click="runSecondaryAction">{{ secondaryActionLabel }}</button>
          <button
            class="button primary"
            :disabled="!canApplyCurrent"
            @click="runPrimaryAction"
          >{{ primaryActionLabel }}</button>
        </div>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.hardware-event-picker {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 10px;
}
.hardware-event-picker button {
  min-height: 25px;
  padding: 0 8px;
  border: 1px solid var(--line);
  border-radius: 7px;
  color: var(--muted);
  background: var(--canvas);
  font-size: 8px;
}
.hardware-event-picker button.active {
  border-color: rgba(78,104,246,.32);
  color: var(--accent-strong);
  background: var(--accent-soft);
}
.mode-row.clickable {
  cursor: pointer;
}
.mode-row.clickable:focus-visible {
  outline: 2px solid rgba(78,104,246,.42);
  outline-offset: 2px;
}
.mode-row.disabled-option {
  cursor: not-allowed;
  opacity: .46;
}
</style>
