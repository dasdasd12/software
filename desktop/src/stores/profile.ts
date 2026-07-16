import { computed, ref, watch } from "vue";
import { defineStore } from "pinia";
import factoryProfileJson from "../../../config/factory_default_profile.json";
import { cloneProfileDocument, isProfileDocument, normalizeProfileDocument } from "../domain/profile/document";
import type { ProfileDocument } from "../domain/profile/types";
import { loadLocalValue, saveLocalValue } from "../services/localPersistence";
import { BridgeClientError, bridgeRequest, type BridgeEvent } from "../services/bridge";
import type { DeviceInfo } from "./device";

type CompileState = "idle" | "compiling" | "valid" | "error";
type DirtySection = "identity" | "bindings" | "trigger" | "behaviors" | "interactions" | "macros" | "report-rate" | "input-guard" | "other";

interface ProfileWorkspaceRecovery {
  sourceDocument: ProfileDocument;
  draftDocument: ProfileDocument;
  activeScopeId: string;
}

const WORKSPACE_STORAGE_KEY = "kiiie.profile-workspace";
const WORKSPACE_STORAGE_VERSION = 1;

export interface InstallProgress {
  stage: string;
  slot: number;
  percent: number;
  bytesDone?: number;
  bytesTotal?: number;
}

export interface InstallResult {
  slot: number;
  activated: boolean;
  info: DeviceInfo;
}

function cloneFactoryProfile(): ProfileDocument {
  return normalizeProfileDocument(structuredClone(factoryProfileJson) as unknown as ProfileDocument);
}

function cloneProfile(document: ProfileDocument): ProfileDocument {
  return cloneProfileDocument(document);
}

function isWorkspaceRecovery(value: unknown): value is ProfileWorkspaceRecovery {
  if (typeof value !== "object" || value === null) return false;
  const recovery = value as Partial<ProfileWorkspaceRecovery>;
  return isProfileDocument(recovery.sourceDocument)
    && isProfileDocument(recovery.draftDocument)
    && typeof recovery.activeScopeId === "string";
}

function signature(value: unknown): string {
  return JSON.stringify(value);
}

export const useProfileStore = defineStore("profile", () => {
  const recoveredWorkspace = loadLocalValue(
    WORKSPACE_STORAGE_KEY,
    WORKSPACE_STORAGE_VERSION,
    isWorkspaceRecovery,
  );
  const currentFactory = cloneFactoryProfile();
  const recoveredSource = recoveredWorkspace
    ? normalizeProfileDocument(recoveredWorkspace.data.sourceDocument)
    : null;
  const recoveredDraft = recoveredWorkspace
    ? normalizeProfileDocument(recoveredWorkspace.data.draftDocument)
    : null;
  const recoveredFactory = recoveredSource?.identity.profile_id === currentFactory.identity.profile_id;
  const recoveredFactoryDirty = Boolean(
    recoveredFactory
    && recoveredSource
    && recoveredDraft
    && signature(recoveredSource) !== signature(recoveredDraft),
  );
  const initialSource = recoveredFactory
    ? currentFactory
    : recoveredSource ?? currentFactory;
  const initialDraft = recoveredFactory
    ? recoveredFactoryDirty && recoveredDraft ? recoveredDraft : currentFactory
    : recoveredDraft ?? initialSource;
  const sourceDocument = ref<ProfileDocument>(cloneProfile(initialSource));
  const draftDocument = ref<ProfileDocument>(cloneProfile(initialDraft));
  const activeScopeId = ref(recoveredWorkspace?.data.activeScopeId ?? "base");
  const recoveredAt = ref<string | null>(recoveredWorkspace?.savedAt ?? null);
  const dirtyControlIds = ref<string[]>([]);
  const compileState = ref<CompileState>("idle");
  const compileMessage = ref("尚未验证本地草稿");
  const writeState = ref<"idle" | "writing" | "partial" | "error" | "done">("idle");
  const writeProgress = ref<InstallProgress | null>(null);

  const identity = computed(() => draftDocument.value.identity);
  const activeScope = computed(() => draftDocument.value.binding_scopes[activeScopeId.value]);
  const sourceScope = computed(() => sourceDocument.value.binding_scopes[activeScopeId.value]);
  const bindingCount = computed(() => Object.keys(activeScope.value?.bindings ?? {}).length);
  const isDirty = computed(() => signature(draftDocument.value) !== signature(sourceDocument.value));
  const dirtySections = computed<DirtySection[]>(() => {
    const draft = draftDocument.value;
    const source = sourceDocument.value;
    const sections: DirtySection[] = [];
    if (signature(draft.identity) !== signature(source.identity)) sections.push("identity");
    if (signature({ scopes: draft.binding_scopes, defaults: draft.defaults.bindings })
      !== signature({ scopes: source.binding_scopes, defaults: source.defaults.bindings })) sections.push("bindings");
    if (signature({ defaults: draft.defaults.triggers, assignments: draft.control_assignments })
      !== signature({ defaults: source.defaults.triggers, assignments: source.control_assignments })) sections.push("trigger");
    if (signature({ definitions: draft.behaviors, defaults: draft.defaults.behaviors })
      !== signature({ definitions: source.behaviors, defaults: source.defaults.behaviors })) sections.push("behaviors");
    if (signature({ rules: draft.interaction_rules, defaults: draft.defaults.interactions })
      !== signature({ rules: source.interaction_rules, defaults: source.defaults.interactions })) sections.push("interactions");
    if (signature(draft.macro_defs) !== signature(source.macro_defs)) sections.push("macros");
    if (signature(draft.report_rate_policy) !== signature(source.report_rate_policy)) sections.push("report-rate");
    if (signature(draft.input_guard_policy) !== signature(source.input_guard_policy)) sections.push("input-guard");
    const knownKeys = new Set([
      "identity", "compatibility", "defaults", "control_assignments", "binding_scopes",
      "behaviors", "interaction_rules", "macro_defs", "report_rate_policy", "input_guard_policy",
    ]);
    const draftOther = Object.fromEntries(Object.entries(draft).filter(([key]) => !knownKeys.has(key)));
    const sourceOther = Object.fromEntries(Object.entries(source).filter(([key]) => !knownKeys.has(key)));
    const knownDefaultKeys = new Set(["triggers", "bindings", "behaviors", "interactions"]);
    const draftOtherDefaults = Object.fromEntries(Object.entries(draft.defaults).filter(([key]) => !knownDefaultKeys.has(key)));
    const sourceOtherDefaults = Object.fromEntries(Object.entries(source.defaults).filter(([key]) => !knownDefaultKeys.has(key)));
    if (signature(draft.compatibility) !== signature(source.compatibility)
      || signature(draftOtherDefaults) !== signature(sourceOtherDefaults)
      || signature(draftOther) !== signature(sourceOther)) sections.push("other");
    return sections;
  });

  function bindingFor(controlId: string): string {
    return activeScope.value?.bindings[controlId] ?? activeScope.value?.unbound ?? "no_op";
  }

  function sourceBindingFor(controlId: string): string {
    return sourceScope.value?.bindings[controlId] ?? sourceScope.value?.unbound ?? "no_op";
  }

  function markDirty(controlId: string): void {
    if (!dirtyControlIds.value.includes(controlId)) dirtyControlIds.value.push(controlId);
  }

  function clearDirtyIfRestored(controlId: string): void {
    if (bindingFor(controlId) === sourceBindingFor(controlId)) {
      dirtyControlIds.value = dirtyControlIds.value.filter((id) => id !== controlId);
    }
  }

  function refreshDirtyControls(): void {
    const draftBindings = activeScope.value?.bindings ?? {};
    const sourceBindings = sourceScope.value?.bindings ?? {};
    const controlIds = new Set([...Object.keys(draftBindings), ...Object.keys(sourceBindings)]);
    dirtyControlIds.value = [...controlIds].filter(
      (controlId) => (draftBindings[controlId] ?? activeScope.value?.unbound ?? "no_op")
        !== (sourceBindings[controlId] ?? sourceScope.value?.unbound ?? "no_op"),
    );
  }

  function markDraftChanged(message = "Profile 草稿已变化，等待重新验证"): void {
    if (compileState.value !== "compiling") compileState.value = "idle";
    compileMessage.value = message;
  }

  function setBinding(controlIds: string[], binding: string): void {
    const scope = activeScope.value;
    if (!scope) return;
    for (const controlId of controlIds) {
      scope.bindings[controlId] = binding;
      markDirty(controlId);
      clearDirtyIfRestored(controlId);
    }
    compileState.value = "idle";
    compileMessage.value = "草稿已变化，等待重新验证";
  }

  function loadDocument(document: ProfileDocument): void {
    if (!isProfileDocument(document)) throw new Error("无法打开：Profile 文档结构无效");
    const normalized = normalizeProfileDocument(document);
    sourceDocument.value = cloneProfile(normalized);
    draftDocument.value = cloneProfile(normalized);
    activeScopeId.value = document.binding_scopes.base ? "base" : Object.keys(document.binding_scopes)[0] ?? "base";
    dirtyControlIds.value = [];
    compileState.value = "idle";
    compileMessage.value = "已打开本地 Profile，尚未验证";
    writeState.value = "idle";
    writeProgress.value = null;
    persistWorkspace();
  }

  function discardDraftChanges(): void {
    draftDocument.value = cloneProfile(sourceDocument.value);
    dirtyControlIds.value = [];
    compileState.value = "idle";
    compileMessage.value = "已放弃未保存的 Profile 更改";
    persistWorkspace();
  }

  function persistWorkspace(): void {
    const savedAt = saveLocalValue<ProfileWorkspaceRecovery>(
      WORKSPACE_STORAGE_KEY,
      WORKSPACE_STORAGE_VERSION,
      {
        sourceDocument: sourceDocument.value,
        draftDocument: draftDocument.value,
        activeScopeId: activeScopeId.value,
      },
    );
    if (savedAt) recoveredAt.value = savedAt;
  }

  let persistTimer: ReturnType<typeof setTimeout> | undefined;
  watch(
    draftDocument,
    () => {
      refreshDirtyControls();
      if (compileState.value !== "compiling") {
        compileState.value = "idle";
        compileMessage.value = isDirty.value ? "Profile 草稿已变化，等待重新验证" : "草稿与来源一致";
      }
      if (persistTimer) clearTimeout(persistTimer);
      persistTimer = setTimeout(persistWorkspace, 180);
    },
    { deep: true },
  );

  watch(activeScopeId, () => {
    refreshDirtyControls();
    persistWorkspace();
  });

  refreshDirtyControls();

  function restoreBindings(controlIds: string[]): void {
    const scope = activeScope.value;
    if (!scope) return;
    for (const controlId of controlIds) {
      scope.bindings[controlId] = sourceBindingFor(controlId);
      clearDirtyIfRestored(controlId);
    }
    compileState.value = "idle";
    compileMessage.value = isDirty.value ? "部分更改已恢复" : "草稿与来源一致";
  }

  async function validateDraft(): Promise<void> {
    compileState.value = "compiling";
    compileMessage.value = "正在通过桌面 Core 编译…";
    try {
      const snapshot = cloneProfile(draftDocument.value);
      const snapshotSignature = JSON.stringify(snapshot);
      await bridgeRequest("profile.compile", { profile: snapshot });
      if (JSON.stringify(draftDocument.value) === snapshotSignature) {
        compileState.value = "valid";
        compileMessage.value = `${bindingCount.value} 条绑定 · 编译通过`;
      } else {
        compileState.value = "idle";
        compileMessage.value = "验证期间草稿发生变化，请重新验证";
      }
    } catch (error) {
      compileState.value = "error";
      compileMessage.value = error instanceof Error ? error.message : String(error);
    }
  }

  async function installToDevice(slot = 1, activate = true): Promise<InstallResult> {
    writeState.value = "writing";
    writeProgress.value = { stage: "compiling", slot, percent: 0 };
    try {
      const snapshot = cloneProfile(draftDocument.value);
      const result = await bridgeRequest<InstallResult>(
        "profile.install",
        { profile: snapshot, slot, activate },
        {
          onEvent(event: BridgeEvent) {
            if (event.event !== "profile.install.progress") return;
            writeProgress.value = event.data as unknown as InstallProgress;
          },
        },
      );
      writeState.value = "done";
      persistWorkspace();
      return result;
    } catch (error) {
      const committed =
        error instanceof BridgeClientError
        && typeof error.details === "object"
        && error.details !== null
        && (error.details as Record<string, unknown>).committed === true;
      if (committed) {
        writeState.value = "partial";
        persistWorkspace();
      } else {
        writeState.value = "error";
      }
      compileMessage.value = error instanceof Error ? error.message : String(error);
      throw error;
    }
  }

  return {
    sourceDocument,
    draftDocument,
    activeScopeId,
    dirtyControlIds,
    compileState,
    compileMessage,
    writeState,
    writeProgress,
    identity,
    activeScope,
    bindingCount,
    isDirty,
    dirtySections,
    recoveredAt,
    bindingFor,
    sourceBindingFor,
    setBinding,
    restoreBindings,
    markDraftChanged,
    loadDocument,
    discardDraftChanges,
    persistWorkspace,
    validateDraft,
    installToDevice,
  };
});
