<script setup lang="ts">
import { computed, ref } from "vue";
import { onBeforeRouteLeave } from "vue-router";
import KeyboardBoard from "../components/keyboard/KeyboardBoard.vue";
import { useUnsavedChangesGuard } from "../composables/useUnsavedChangesGuard";
import { getKeyboardKey } from "../domain/keyboard/layout";
import type {
  DksBehavior,
  DksStage,
  ProfileBehavior,
  SocdInteractionRule,
  SocdResolution,
} from "../domain/profile/types";
import { loadLocalValue, saveLocalValue } from "../services/localPersistence";
import { useProfileStore } from "../stores/profile";

type Workspace = "dks" | "socd";
type DksTab = "behavior" | "stages" | "validation";
type SocdTab = "rule" | "members" | "validation";

interface DksStageDraft {
  id: string;
  minUm: number;
  maxUm: number;
  enter: string;
}

interface BindingOrigin {
  scopeId: string;
  controlId: string;
  explicit: boolean;
  target: string;
}

interface DksOriginStore {
  origins: Record<string, BindingOrigin>;
}

const DIRECT_TARGET = /^(keyboard|consumer|mouse)\.[a-z0-9_]+$/;
const DKS_ORIGIN_STORAGE_KEY = "kiiie.dks-binding-origins";
const DKS_ORIGIN_STORAGE_VERSION = 1;
const FALLBACK_TARGETS = [
  "no_op",
  "keyboard.w",
  "keyboard.a",
  "keyboard.s",
  "keyboard.d",
  "keyboard.space",
  "keyboard.left_shift",
  "keyboard.left_ctrl",
  "keyboard.enter",
  "keyboard.escape",
];

const profile = useProfileStore();
const workspace = ref<Workspace>("dks");
const dksTab = ref<DksTab>("stages");
const socdTab = ref<SocdTab>("rule");
const dksSelectedIds = ref<string[]>([]);
const socdSelectedIds = ref<string[]>([]);
const dksStages = ref<DksStageDraft[]>([]);
const activeStageIndex = ref<number | null>(null);
const dksBehaviorId = ref<string | null>(null);
const dksOriginalTarget = ref("no_op");
const dksBufferDirty = ref(false);
const dksStatus = ref("未选择目标键；工作区不会自动选中任何控件");
const socdRuleId = ref<string | null>(null);
const socdResolution = ref<SocdResolution>("last_input");
const socdBufferDirty = ref(false);
const socdStatus = ref("先选择两个不同的磁轴键；选择本身不会修改 Profile");
const dksOriginalBinding = ref<BindingOrigin | null>(null);
const restoredOrigins = loadLocalValue(
  DKS_ORIGIN_STORAGE_KEY,
  DKS_ORIGIN_STORAGE_VERSION,
  isDksOriginStore,
)?.data.origins ?? {};
const dksRestoreOrigins = new Map<string, BindingOrigin>(Object.entries(restoredOrigins));

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isBindingOrigin(value: unknown): value is BindingOrigin {
  return isPlainObject(value)
    && typeof value.scopeId === "string"
    && typeof value.controlId === "string"
    && typeof value.explicit === "boolean"
    && typeof value.target === "string";
}

function isDksOriginStore(value: unknown): value is DksOriginStore {
  return isPlainObject(value)
    && isPlainObject(value.origins)
    && Object.values(value.origins).every(isBindingOrigin);
}

function dksOriginKey(behaviorId: string): string {
  return `${profile.identity.profile_id}:${behaviorId}`;
}

function persistDksOrigins(): void {
  saveLocalValue<DksOriginStore>(
    DKS_ORIGIN_STORAGE_KEY,
    DKS_ORIGIN_STORAGE_VERSION,
    { origins: Object.fromEntries(dksRestoreOrigins) },
  );
}

function isDksStage(value: unknown): value is DksStage {
  if (!isPlainObject(value) || !isPlainObject(value.range_um)) return false;
  return Number.isInteger(value.range_um.min_um)
    && Number.isInteger(value.range_um.max_um)
    && typeof value.enter === "string";
}

function isDksBehavior(value: ProfileBehavior | undefined): value is DksBehavior {
  return value?.kind === "dks"
    && value.source === "travel"
    && Array.isArray(value.stages)
    && value.stages.every(isDksStage);
}

function isSocdRule(value: unknown): value is SocdInteractionRule {
  return isPlainObject(value)
    && value.kind === "socd"
    && Array.isArray(value.members)
    && value.members.length === 2
    && value.members.every((member) => typeof member === "string")
    && value.members[0] !== value.members[1]
    && (value.resolution === "neutral"
      || value.resolution === "last_input"
      || value.resolution === "absolute_priority");
}

function isDirectTarget(value: unknown): value is string {
  return typeof value === "string" && (value === "no_op" || DIRECT_TARGET.test(value));
}

function defaultSocdResolution(): SocdResolution {
  const interactions = profile.draftDocument.defaults.interactions as
    | { socd_resolution?: unknown }
    | undefined;
  const value = interactions?.socd_resolution;
  return value === "neutral" || value === "last_input"
    ? value
    : "last_input";
}

const selectedIds = computed(() =>
  workspace.value === "dks" ? dksSelectedIds.value : socdSelectedIds.value,
);
const hasTarget = computed(() => selectedIds.value.length > 0);
const dksKey = computed(() => getKeyboardKey(dksSelectedIds.value[0]));
const socdKeys = computed(() => socdSelectedIds.value.map(getKeyboardKey).filter(Boolean));
const socdTitle = computed(() => socdKeys.value.map((key) => key?.label).join(" + ") || "选择成员");
const activeStage = computed(() =>
  activeStageIndex.value === null ? null : dksStages.value[activeStageIndex.value] ?? null,
);
const dksTargetOptions = computed(() => {
  const values = Object.values(profile.draftDocument.binding_scopes)
    .flatMap((scope) => Object.values(scope.bindings))
    .filter(isDirectTarget);
  return Array.from(new Set([...FALLBACK_TARGETS, dksOriginalTarget.value, ...values].filter(isDirectTarget)))
    .sort((a, b) => a.localeCompare(b));
});
const dksValidationErrors = computed(() => {
  const errors: string[] = [];
  if (dksStages.value.length === 0) errors.push("至少需要一个行程区间");
  const ordered = [...dksStages.value].sort((a, b) => a.minUm - b.minUm || a.maxUm - b.maxUm);
  ordered.forEach((stage, index) => {
    if (!Number.isInteger(stage.minUm) || !Number.isInteger(stage.maxUm)) {
      errors.push(`S${index + 1} 的边界必须是整数微米`);
    }
    if (stage.minUm < 0 || stage.maxUm > 4000 || stage.minUm >= stage.maxUm) {
      errors.push(`S${index + 1} 的范围必须位于 0–4000 µm，且起点小于终点`);
    }
    if (!isDirectTarget(stage.enter)) errors.push(`S${index + 1} 的进入动作无效`);
    if (index > 0 && stage.minUm < ordered[index - 1].maxUm) {
      errors.push(`S${index} 与 S${index + 1} 的区间发生重叠`);
    }
  });
  return errors;
});
const canSave = computed(() => workspace.value === "dks"
  ? dksSelectedIds.value.length === 1
    && dksValidationErrors.value.length === 0
    && (dksBufferDirty.value || !dksBehaviorId.value)
  : socdSelectedIds.value.length === 2
    && socdResolution.value !== "absolute_priority"
    && (socdBufferDirty.value || !socdRuleId.value));
const localDirty = computed(() => workspace.value === "dks" ? dksBufferDirty.value : socdBufferDirty.value);
const localStatus = computed(() => workspace.value === "dks" ? dksStatus.value : socdStatus.value);
const inspectorTitle = computed(() => {
  if (!hasTarget.value) return "未选择控件";
  return workspace.value === "dks" ? dksKey.value?.label ?? "选择按键" : socdTitle.value;
});
const inspectorId = computed(() => {
  if (!hasTarget.value) return "";
  return workspace.value === "dks" ? dksSelectedIds.value[0] : socdRuleId.value ?? "NEW SOCD";
});
const currentBehaviorReferenceCount = computed(() =>
  dksBehaviorId.value ? behaviorReferenceCount(dksBehaviorId.value) : 0,
);
const saveButtonLabel = computed(() => {
  if (!hasTarget.value) return "选择控件后可保存";
  if (workspace.value === "socd") {
    if (socdSelectedIds.value.length < 2) return "还需一个规则成员";
    if (socdResolution.value === "absolute_priority") return "绝对优先契约待补齐";
    if (socdRuleId.value && !socdBufferDirty.value) return "规则已保存";
  }
  if (workspace.value === "dks" && dksBehaviorId.value && !dksBufferDirty.value) return "行为已保存";
  return "保存到 Profile source";
});

function setWorkspace(next: Workspace): void {
  if (next === workspace.value) return;
  if (!confirmDiscardBuffer()) return;
  if (localDirty.value) discardBuffer();
  workspace.value = next;
}

function confirmDiscardBuffer(): boolean {
  return !localDirty.value || window.confirm("放弃当前高级行为编辑缓冲区中的未保存修改？");
}

onBeforeRouteLeave(() => confirmDiscardBuffer());
useUnsavedChangesGuard(
  () => localDirty.value,
  "高级行为编辑器中还有尚未保存到 Profile 草稿的修改。",
);

function countStringReferences(value: unknown, target: string): number {
  if (value === target) return 1;
  if (Array.isArray(value)) return value.reduce((total, item) => total + countStringReferences(item, target), 0);
  if (typeof value !== "object" || value === null) return 0;
  return Object.values(value).reduce((total, item) => total + countStringReferences(item, target), 0);
}

function behaviorReferenceCount(behaviorId: string): number {
  let count = 0;
  for (const scope of Object.values(profile.draftDocument.binding_scopes)) {
    count += Object.values(scope.bindings).filter((target) => target === behaviorId).length;
  }
  for (const [candidateId, behavior] of Object.entries(profile.draftDocument.behaviors)) {
    if (candidateId !== behaviorId) count += countStringReferences(behavior, behaviorId);
  }
  return count;
}

function uniqueBehaviorId(controlId: string): string {
  const base = `b_dks_${controlId.replace(/[^a-z0-9_]/gi, "_").toLowerCase()}`;
  let candidate = base;
  let suffix = 2;
  while (profile.draftDocument.behaviors[candidate]) {
    candidate = `${base}_${suffix}`;
    suffix += 1;
  }
  return candidate;
}

function uniqueRuleId(members: string[]): string {
  const base = `socd_${members.map((id) => id.replace(/^key_/, "")).join("_")}`;
  const existing = new Set(profile.draftDocument.interaction_rules.map((rule) => rule.rule_id));
  let candidate = base;
  let suffix = 2;
  while (existing.has(candidate)) {
    candidate = `${base}_${suffix}`;
    suffix += 1;
  }
  return candidate;
}

function membersMatch(left: string[], right: string[]): boolean {
  return left.length === right.length && [...left].sort().every((member, index) => member === [...right].sort()[index]);
}

function loadDksForKey(controlId: string): void {
  const scope = profile.activeScope;
  const target = profile.bindingFor(controlId);
  const behavior = profile.draftDocument.behaviors[target];
  dksOriginalBinding.value = {
    scopeId: profile.activeScopeId,
    controlId,
    explicit: Boolean(scope && Object.prototype.hasOwnProperty.call(scope.bindings, controlId)),
    target,
  };
  dksOriginalTarget.value = target;
  dksBufferDirty.value = false;

  if (isDksBehavior(behavior)) {
    dksBehaviorId.value = target;
    dksStages.value = behavior.stages.map((stage, index) => ({
      id: `S${index + 1}`,
      minUm: stage.range_um.min_um,
      maxUm: stage.range_um.max_um,
      enter: stage.enter,
    }));
    activeStageIndex.value = dksStages.value.length ? 0 : null;
    dksStatus.value = `已载入 ${target}；修改前不会触碰 Profile`;
    return;
  }

  dksBehaviorId.value = null;
  const preservedTarget = isDirectTarget(target) ? target : "no_op";
  dksStages.value = [{ id: "S1", minUm: 200, maxUm: 4000, enter: preservedTarget }];
  activeStageIndex.value = 0;
  dksStatus.value = isDirectTarget(target)
    ? `新 DKS 草稿会先保留当前输出 ${target}`
    : `当前绑定 ${target} 不是直接 HID 输出；保存前请为区间选择动作`;
}

function findSocdRule(members: string[]): SocdInteractionRule | undefined {
  return profile.draftDocument.interaction_rules
    .filter(isSocdRule)
    .find((rule) => membersMatch(rule.members, members));
}

function loadSocdMembers(members: string[], allowSingleLookup = false): void {
  socdSelectedIds.value = members;
  socdBufferDirty.value = false;
  let rule: SocdInteractionRule | undefined;
  if (members.length === 2) rule = findSocdRule(members);
  if (!rule && allowSingleLookup && members.length === 1) {
    rule = profile.draftDocument.interaction_rules
      .filter(isSocdRule)
      .find((candidate) => candidate.members.includes(members[0]));
    if (rule) socdSelectedIds.value = [...rule.members];
  }

  if (rule) {
    socdSelectedIds.value = [...rule.members];
    socdRuleId.value = rule.rule_id;
    socdResolution.value = rule.resolution;
    socdStatus.value = rule.resolution === "absolute_priority"
      ? `已载入 ${rule.rule_id}；绝对优先成员契约未定义，当前只读`
      : `已载入 ${rule.rule_id}；修改前不会触碰 Profile`;
  } else {
    socdRuleId.value = null;
    socdResolution.value = defaultSocdResolution();
    socdStatus.value = members.length === 2
      ? "新的 SOCD 规则草稿；成员顺序会保留在 Profile 中"
      : "再选择一个不同的磁轴键";
  }
}

function selectControl(payload: { id: string; additive: boolean; range: boolean }): void {
  if (!payload.id.startsWith("key_")) {
    if (workspace.value === "dks") dksStatus.value = "高级行为当前只面向磁轴按键";
    else socdStatus.value = "SOCD 成员当前只面向磁轴按键";
    return;
  }

  if (workspace.value === "dks") {
    if (dksSelectedIds.value[0] === payload.id) return;
    if (!confirmDiscardBuffer()) return;
    dksSelectedIds.value = [payload.id];
    loadDksForKey(payload.id);
    return;
  }

  if (!confirmDiscardBuffer()) return;

  if (socdSelectedIds.value.length === 0) {
    loadSocdMembers([payload.id], true);
  } else if (socdSelectedIds.value.includes(payload.id)) {
    loadSocdMembers(socdSelectedIds.value.filter((id) => id !== payload.id));
  } else if (socdSelectedIds.value.length === 1) {
    loadSocdMembers([...socdSelectedIds.value, payload.id]);
  } else {
    loadSocdMembers([socdSelectedIds.value[0], payload.id]);
  }
}

function clearSelection(): void {
  if (!confirmDiscardBuffer()) return;
  if (workspace.value === "dks") {
    dksSelectedIds.value = [];
    dksStages.value = [];
    activeStageIndex.value = null;
    dksBehaviorId.value = null;
    dksOriginalBinding.value = null;
    dksBufferDirty.value = false;
    dksStatus.value = "已清除选择；Profile 未发生变化";
  } else {
    socdSelectedIds.value = [];
    socdRuleId.value = null;
    socdBufferDirty.value = false;
    socdStatus.value = "已清除选择；Profile 未发生变化";
  }
}

function markDksDirty(): void {
  dksBufferDirty.value = true;
  dksStatus.value = "行程区间草稿有未保存修改";
}

function selectStage(index: number): void {
  activeStageIndex.value = index;
}

function addStage(): void {
  if (dksStages.value.length >= 6) {
    dksStatus.value = "当前编辑器最多显示 6 个区间";
    return;
  }
  if (!activeStage.value) {
    dksStages.value.push({ id: "S1", minUm: 200, maxUm: 4000, enter: "no_op" });
    activeStageIndex.value = 0;
    markDksDirty();
    return;
  }
  const index = activeStageIndex.value ?? 0;
  const stage = dksStages.value[index];
  const width = stage.maxUm - stage.minUm;
  if (width < 200) {
    dksStatus.value = "当前区间至少需要 200 µm 宽度才能拆分";
    return;
  }
  const previousMax = stage.maxUm;
  const midpoint = Math.round(((stage.minUm + stage.maxUm) / 2) / 100) * 100;
  stage.maxUm = midpoint;
  dksStages.value.splice(index + 1, 0, {
    id: "",
    minUm: midpoint,
    maxUm: previousMax,
    enter: stage.enter,
  });
  dksStages.value.forEach((item, stageIndex) => { item.id = `S${stageIndex + 1}`; });
  activeStageIndex.value = index + 1;
  markDksDirty();
}

function removeActiveStage(): void {
  if (activeStageIndex.value === null || dksStages.value.length <= 1) return;
  dksStages.value.splice(activeStageIndex.value, 1);
  dksStages.value.forEach((item, index) => { item.id = `S${index + 1}`; });
  activeStageIndex.value = Math.min(activeStageIndex.value, dksStages.value.length - 1);
  markDksDirty();
}

function discardBuffer(): void {
  if (workspace.value === "dks" && dksSelectedIds.value[0]) {
    loadDksForKey(dksSelectedIds.value[0]);
  } else if (workspace.value === "socd") {
    loadSocdMembers([...socdSelectedIds.value]);
  }
}

function saveDks(): void {
  const controlId = dksSelectedIds.value[0];
  if (!controlId || dksValidationErrors.value.length) return;
  const originalBehaviorId = dksBehaviorId.value;
  const createsReplacement = !originalBehaviorId || behaviorReferenceCount(originalBehaviorId) > 1;
  if (createsReplacement && !isDirectTarget(dksOriginalTarget.value)) {
    const accepted = window.confirm(`当前绑定是 ${dksOriginalTarget.value}。保存 DKS 会只替换当前键的这条复杂绑定，是否继续？`);
    if (!accepted) return;
  }
  const behaviorId = createsReplacement ? uniqueBehaviorId(controlId) : originalBehaviorId;
  if (!behaviorId) return;

  const stages: DksStage[] = [...dksStages.value]
    .sort((a, b) => a.minUm - b.minUm || a.maxUm - b.maxUm)
    .map((stage) => ({
      range_um: { min_um: Math.round(stage.minUm), max_um: Math.round(stage.maxUm) },
      enter: stage.enter,
    }));
  const existing = profile.draftDocument.behaviors[behaviorId];
  profile.draftDocument.behaviors[behaviorId] = {
    ...(existing ?? {}),
    kind: "dks",
    source: "travel",
    stages,
  };
  if (createsReplacement && dksOriginalBinding.value) {
    dksRestoreOrigins.set(dksOriginKey(behaviorId), { ...dksOriginalBinding.value });
    persistDksOrigins();
  }
  profile.setBinding([controlId], behaviorId);
  dksBehaviorId.value = behaviorId;
  dksBufferDirty.value = false;
  dksStatus.value = `已保存到 Profile source：${behaviorId}；RuntimeTable/固件暂不执行 DKS`;
  profile.markDraftChanged("DKS 已写入 Profile source；运行时支持仍待实现");
  if (originalBehaviorId && originalBehaviorId !== behaviorId && behaviorReferenceCount(originalBehaviorId) === 0) {
    delete profile.draftDocument.behaviors[originalBehaviorId];
  }
}

function removeDksFromKey(): void {
  const controlId = dksSelectedIds.value[0];
  const behaviorId = dksBehaviorId.value;
  if (!controlId || !behaviorId) return;
  const originKey = dksOriginKey(behaviorId);
  const origin = dksRestoreOrigins.get(originKey);
  const canRestoreOrigin = origin
    && origin.controlId === controlId
    && origin.scopeId === profile.activeScopeId;
  const replacement = canRestoreOrigin ? origin.target : dksStages.value[0]?.enter ?? "no_op";
  const message = canRestoreOrigin
    ? `从 ${controlId} 移除 DKS，并恢复原绑定 ${replacement}？`
    : `这份 DKS 没有可追溯的原绑定。从 ${controlId} 移除后将使用首段输出 ${replacement}，是否继续？`;
  if (!window.confirm(message)) return;
  const scope = profile.activeScope;
  if (canRestoreOrigin && !origin.explicit && scope) {
    delete scope.bindings[controlId];
  } else {
    profile.setBinding([controlId], replacement);
  }
  if (behaviorReferenceCount(behaviorId) === 0) delete profile.draftDocument.behaviors[behaviorId];
  dksRestoreOrigins.delete(originKey);
  persistDksOrigins();
  profile.markDraftChanged("已从所选键移除 DKS；更改仅在本地 Profile 草稿中");
  loadDksForKey(controlId);
  dksStatus.value = `已移除 DKS，当前映射为 ${replacement}`;
}

function setSocdResolution(next: SocdResolution): void {
  socdResolution.value = next;
  socdBufferDirty.value = true;
  socdStatus.value = "SOCD 规则草稿有未保存修改";
}

function saveSocd(): void {
  if (socdSelectedIds.value.length !== 2 || socdResolution.value === "absolute_priority") return;
  const duplicate = findSocdRule(socdSelectedIds.value);
  const ruleId = socdRuleId.value ?? duplicate?.rule_id ?? uniqueRuleId(socdSelectedIds.value);
  const existingRule = profile.draftDocument.interaction_rules.find((item) => item.rule_id === ruleId);
  const rule: SocdInteractionRule = {
    ...(existingRule ?? {}),
    rule_id: ruleId,
    kind: "socd",
    members: [socdSelectedIds.value[0], socdSelectedIds.value[1]],
    resolution: socdResolution.value,
  };
  const index = profile.draftDocument.interaction_rules.findIndex((item) => item.rule_id === ruleId);
  if (index >= 0) profile.draftDocument.interaction_rules[index] = rule;
  else profile.draftDocument.interaction_rules.push(rule);
  socdRuleId.value = ruleId;
  socdBufferDirty.value = false;
  socdStatus.value = `已保存到 Profile source：${ruleId}；RuntimeTable/固件暂不执行 SOCD`;
  profile.markDraftChanged("SOCD 已写入 Profile source；运行时支持仍待实现");
}

function deleteSocd(): void {
  const ruleId = socdRuleId.value;
  if (!ruleId || !window.confirm(`从 Profile 草稿删除 ${ruleId}？`)) return;
  profile.draftDocument.interaction_rules = profile.draftDocument.interaction_rules
    .filter((rule) => rule.rule_id !== ruleId);
  socdRuleId.value = null;
  socdBufferDirty.value = false;
  socdResolution.value = defaultSocdResolution();
  socdStatus.value = `已从 Profile 草稿删除 ${ruleId}`;
  profile.markDraftChanged("已删除 SOCD 规则；更改仅在本地 Profile 草稿中");
}

function saveCurrent(): void {
  if (workspace.value === "dks") saveDks();
  else saveSocd();
}
</script>

<template>
  <div class="page-shell">
    <section class="page-main keymap-main">
      <header class="page-heading">
        <div>
          <p class="page-kicker">PROFILE / ADVANCED BEHAVIOR</p>
          <h1>{{ workspace === "dks" ? "高级行为" : "多键规则" }}</h1>
          <p class="lede">
            {{ workspace === "dks"
              ? "用真实行程区间组织 DKS；界面显示毫米，Profile 始终保存整数微米。"
              : "直接在真实配列上选择两个成员，再定义同时输入时的裁决方式。" }}
          </p>
        </div>
        <div class="heading-actions">
          <div class="segmented" aria-label="高级行为工作区">
            <button :class="{ active: workspace === 'dks' }" @click="setWorkspace('dks')">DKS</button>
            <button :class="{ active: workspace === 'socd' }" @click="setWorkspace('socd')">SOCD</button>
          </div>
        </div>
      </header>

      <div class="keyboard-intro">
        <span><small>77 键 · CAD 实际坐标 · Profile source 编辑</small><b>AK Ergo 77</b></span>
        <span class="chip" :class="workspace === 'dks' ? 'blue' : 'coral'">
          {{ workspace === "dks" ? "单键行程区间" : "双键冲突裁决" }}
        </span>
      </div>

      <section class="keyboard-surface">
        <KeyboardBoard :selected-ids="selectedIds" @select="selectControl" @clear="clearSelection" />
        <div class="keyboard-toolbar">
          <span v-if="workspace === 'dks'"><kbd>Click</kbd>选择一个 DKS 目标键　点击空白处清除</span>
          <span v-else><kbd>Click</kbd>依次选择两个规则成员　已有规则会自动载入</span>
          <div class="key-groups">
            <button class="active">{{ workspace === "dks" ? "动作键" : "SOCD 成员" }}</button>
            <button :disabled="!hasTarget" @click="clearSelection">清除选择</button>
          </div>
          <span class="selection-count">{{ hasTarget ? `${selectedIds.length} 个控件` : "未选择" }}</span>
        </div>
      </section>

      <div class="status-strip" aria-live="polite">
        <div class="status-item">
          <i class="status-icon" :class="{ good: !localDirty && hasTarget }">{{ localDirty ? "•" : "P" }}</i>
          <p><b>{{ localDirty ? "编辑缓冲区有修改" : "Profile source 编辑器" }}</b><small>{{ localStatus }}</small></p>
        </div>
        <div class="status-item">
          <i class="status-icon">⌁</i>
          <p><b>运行时能力待实现</b><small>字段可存档；当前 RuntimeTable 与固件不会执行</small></p>
        </div>
        <button class="status-link" :disabled="!canSave" @click="saveCurrent">保存到 Profile →</button>
      </div>
    </section>

    <aside class="page-inspector">
      <div class="inspector-inner">
        <header class="inspector-head">
          <p class="tiny-label">{{ workspace === "dks" ? "高级行为" : "交互规则" }}</p>
          <h2>{{ inspectorTitle }} <small v-if="hasTarget">{{ inspectorId }}</small></h2>
          <p>{{ hasTarget
            ? workspace === "dks" ? "DKS · 当前绑定域 · Profile source" : "跨按键裁决 · Profile source"
            : workspace === "dks" ? "选择一个磁轴键后编辑行程区间" : "依次选择两个磁轴键作为规则成员" }}</p>
        </header>

        <nav v-if="workspace === 'dks'" class="inspector-tabs" role="tablist" aria-label="DKS 编辑页签">
          <button role="tab" :aria-selected="dksTab === 'behavior'" :class="{ active: dksTab === 'behavior' }" @click="dksTab = 'behavior'">行为</button>
          <button role="tab" :aria-selected="dksTab === 'stages'" :class="{ active: dksTab === 'stages' }" @click="dksTab = 'stages'">区间</button>
          <button role="tab" :aria-selected="dksTab === 'validation'" :class="{ active: dksTab === 'validation' }" @click="dksTab = 'validation'">校验</button>
        </nav>
        <nav v-else class="inspector-tabs" role="tablist" aria-label="SOCD 编辑页签">
          <button role="tab" :aria-selected="socdTab === 'rule'" :class="{ active: socdTab === 'rule' }" @click="socdTab = 'rule'">规则</button>
          <button role="tab" :aria-selected="socdTab === 'members'" :class="{ active: socdTab === 'members' }" @click="socdTab = 'members'">成员</button>
          <button role="tab" :aria-selected="socdTab === 'validation'" :class="{ active: socdTab === 'validation' }" @click="socdTab = 'validation'">校验</button>
        </nav>

        <div class="inspector-scroll">
          <div v-if="!hasTarget" class="selection-empty" role="status">
            <div class="selection-empty__key" aria-hidden="true"><span></span></div>
            <p class="tiny-label">{{ workspace === "dks" ? "DKS TARGET" : "SOCD MEMBERS" }}</p>
            <h3>{{ workspace === "dks" ? "选择一个行程目标键" : "从第一个规则成员开始" }}</h3>
            <p>工作区与页签可以自由浏览。只有选中控件并点击保存，才会修改 Profile 草稿。</p>
          </div>

          <template v-else-if="workspace === 'dks'">
            <template v-if="dksTab === 'behavior'">
              <section class="inspector-section">
                <div class="section-title"><h3>行为类型</h3><span class="chip blue">dks / travel</span></div>
                <div class="segmented behavior-tabs"><button disabled>Tap-Hold</button><button class="active">DKS</button><button disabled>Overlay</button></div>
              </section>
              <section class="inspector-section"><div class="notice">标尺按 0–4.0 mm 显示，序列化时会精确转换为整数 µm；校准数据仍由设备数据层管理。</div></section>
              <section class="inspector-section">
                <div class="profile-meta-grid">
                  <span>Behavior ID</span><b>{{ dksBehaviorId ?? "保存时生成" }}</b>
                  <span>当前引用</span><b>{{ currentBehaviorReferenceCount }}</b>
                  <span>原绑定</span><b>{{ dksOriginalTarget }}</b>
                  <span>保存策略</span><b>{{ currentBehaviorReferenceCount > 1 ? "复制后只绑定当前键" : "原位更新" }}</b>
                </div>
              </section>
              <section class="inspector-section"><div class="notice">第一版区间动作只允许直接 HID 输出，避免生成循环 behavior 引用。</div></section>
            </template>

            <template v-else-if="dksTab === 'stages'">
              <section class="inspector-section dks-section">
                <div class="section-title"><h3>行程区间</h3><span>{{ dksStages.length }} 个 stage</span></div>
                <div class="dks-editor">
                  <div class="travel-rail">
                    <span class="travel-fill"></span>
                    <i
                      v-for="(stage, index) in dksStages"
                      :key="stage.id"
                      :class="{ active: activeStageIndex === index }"
                      :style="{ bottom: `${Math.min(96, stage.maxUm / 4000 * 100)}%` }"
                      @click="selectStage(index)"
                    ><b>{{ stage.id }}</b></i>
                    <small class="rail-top">4.0 mm</small><small class="rail-bottom">0 mm</small>
                  </div>
                  <div class="dks-actions">
                    <div
                      v-for="(stage, index) in dksStages"
                      :key="stage.id"
                      role="button"
                      tabindex="0"
                      @click="selectStage(index)"
                      @keydown.enter="selectStage(index)"
                      @keydown.space.prevent="selectStage(index)"
                    >
                      <span>{{ stage.id }} · {{ (stage.minUm / 1000).toFixed(1) }}–{{ (stage.maxUm / 1000).toFixed(1) }} mm</span>
                      <b>进入区间</b>
                      <i class="chip" :class="activeStageIndex === index ? 'blue' : ''">{{ stage.enter }}</i>
                    </div>
                  </div>
                </div>
              </section>
              <section v-if="activeStage" class="inspector-section stage-editor">
                <div class="section-title"><h3>{{ activeStage.id }} 区间设置</h3><span>Profile 保存 µm</span></div>
                <label class="native-parameter">
                  <span>起点</span><b>{{ (activeStage.minUm / 1000).toFixed(1) }} mm</b>
                  <input v-model.number="activeStage.minUm" type="range" min="0" max="3900" step="100" @input="markDksDirty" />
                </label>
                <label class="native-parameter">
                  <span>终点</span><b>{{ (activeStage.maxUm / 1000).toFixed(1) }} mm</b>
                  <input v-model.number="activeStage.maxUm" type="range" min="100" max="4000" step="100" @input="markDksDirty" />
                </label>
                <label class="field-label" :for="`dks-target-${activeStage.id}`">进入区间时执行</label>
                <div class="select-field binding-select-field compact-select">
                  <span><strong>{{ activeStage.enter }}</strong><small>direct target</small></span>
                  <select :id="`dks-target-${activeStage.id}`" v-model="activeStage.enter" @change="markDksDirty">
                    <option v-for="target in dksTargetOptions" :key="target" :value="target">{{ target }}</option>
                  </select>
                  <i class="field-chevron">⌄</i>
                </div>
                <div class="stage-actions">
                  <button class="button quiet" :disabled="dksStages.length >= 6" @click="addStage">拆分此区间</button>
                  <button class="button quiet danger-text" :disabled="dksStages.length <= 1" @click="removeActiveStage">删除区间</button>
                </div>
              </section>
            </template>

            <template v-else>
              <section class="inspector-section">
                <div class="section-title"><h3>Profile 校验</h3><span class="chip" :class="dksValidationErrors.length ? 'coral' : 'blue'">{{ dksValidationErrors.length ? `${dksValidationErrors.length} 项` : "结构有效" }}</span></div>
                <div class="profile-meta-grid">
                  <span>目标键</span><b>{{ dksSelectedIds[0] }}</b>
                  <span>行程区间</span><b>{{ dksStages.length }}</b>
                  <span>Source 字段</span><b>behaviors + binding</b>
                  <span>Runtime</span><b class="coral-text">待实现</b>
                </div>
              </section>
              <section v-if="dksValidationErrors.length" class="inspector-section">
                <div v-for="error in dksValidationErrors" :key="error" class="notice warning">{{ error }}</div>
              </section>
              <section v-else class="inspector-section"><div class="notice">保存会生成规范整数 µm 区间；当前桌面编译器会保留 source，但绑定 DKS 时仍会报告运行时不支持。</div></section>
              <section v-if="dksBehaviorId" class="inspector-section">
                <button class="inline-link danger-text" @click="removeDksFromKey">从当前键移除 DKS <span>恢复首段输出 →</span></button>
              </section>
            </template>
          </template>

          <template v-else>
            <template v-if="socdTab === 'rule'">
              <section class="inspector-section">
                <div class="section-title"><h3>规则类型</h3><span class="chip blue">{{ socdRuleId ?? "保存时生成 ID" }}</span></div>
                <div class="segmented rule-tabs"><button class="active">SOCD</button><button disabled>Combo</button></div>
              </section>
              <section class="inspector-section">
                <div class="section-title"><h3>裁决策略</h3><span>resolution</span></div>
                <div class="resolution-list">
                  <div :class="{ active: socdResolution === 'neutral' }" role="button" tabindex="0" @click="setSocdResolution('neutral')" @keydown.enter="setSocdResolution('neutral')" @keydown.space.prevent="setSocdResolution('neutral')"><i class="radio" :class="{ active: socdResolution === 'neutral' }"></i><span><b>回到中立</b><small>neutral</small></span></div>
                  <div :class="{ active: socdResolution === 'last_input' }" role="button" tabindex="0" @click="setSocdResolution('last_input')" @keydown.enter="setSocdResolution('last_input')" @keydown.space.prevent="setSocdResolution('last_input')"><i class="radio" :class="{ active: socdResolution === 'last_input' }"></i><span><b>后输入优先</b><small>last_input</small></span></div>
                  <div class="disabled-option" :class="{ active: socdResolution === 'absolute_priority' }" title="Profile v1 尚未定义哪个成员具有绝对优先级"><i class="radio" :class="{ active: socdResolution === 'absolute_priority' }"></i><span><b>绝对优先级</b><small>契约待补齐，暂不可新选</small></span></div>
                </div>
              </section>
              <section class="inspector-section"><div class="notice">SOCD 只保存成员与 resolution。时间窗和抑制成员属于 Combo，不会混入这条规则。</div></section>
            </template>

            <template v-else-if="socdTab === 'members'">
              <section class="inspector-section">
                <div class="section-title"><h3>成员输入</h3><span>{{ socdSelectedIds.length }} / 2 个控件</span></div>
                <div class="member-pair">
                  <span class="key-token selected">{{ socdKeys[0]?.label ?? "—" }}</span><i>＋</i>
                  <span class="key-token coral-key">{{ socdKeys[1]?.label ?? "—" }}</span>
                  <p><b>相反方向</b><small>{{ socdSelectedIds.join(" / ") }}</small></p>
                </div>
              </section>
              <section class="inspector-section"><div class="notice">成员数组会按当前选择顺序写入；SOCD 不会改变两个键原有的输出绑定。</div></section>
            </template>

            <template v-else>
              <section class="inspector-section">
                <div class="section-title"><h3>规则校验</h3><span class="chip" :class="socdSelectedIds.length === 2 ? 'blue' : 'amber'">{{ socdSelectedIds.length === 2 ? "结构有效" : "还缺成员" }}</span></div>
                <div class="profile-meta-grid">
                  <span>成员</span><b>{{ socdTitle }}</b>
                  <span>策略</span><b>{{ socdResolution }}</b>
                  <span>Source 字段</span><b>interaction_rules</b>
                  <span>Runtime</span><b class="coral-text">待实现</b>
                </div>
              </section>
              <section class="inspector-section"><div class="notice warning">AKPK 会完整保留这条规则，但当前 RuntimeTable 的 interaction section 仍为空，因此设备暂不会执行它。</div></section>
              <section v-if="socdRuleId" class="inspector-section">
                <button class="inline-link danger-text" @click="deleteSocd">从 Profile 删除规则 <span>{{ socdRuleId }} →</span></button>
              </section>
            </template>
          </template>
        </div>

        <div class="inspector-actions">
          <button class="button quiet" :disabled="!localDirty" @click="discardBuffer">放弃未保存修改</button>
          <button class="button primary" :disabled="!canSave" @click="saveCurrent">
            {{ saveButtonLabel }}
          </button>
        </div>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.compact-select {
  min-height: 46px;
}

.compact-select span {
  padding-left: 2px;
}

.stage-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin-top: 12px;
}

.stage-actions .button {
  min-width: 0;
}

.danger-text {
  color: var(--coral) !important;
}

.disabled-option {
  cursor: not-allowed !important;
  opacity: .48;
}
</style>
