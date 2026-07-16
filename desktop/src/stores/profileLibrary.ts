import { computed, ref, watch } from "vue";
import { defineStore } from "pinia";
import factoryProfileJson from "../../../config/factory_default_profile.json";
import { isProfileDocument, normalizeProfileDocument } from "../domain/profile/document";
import type { ProfileDocument } from "../domain/profile/types";
import { loadLocalValue, saveLocalValue } from "../services/localPersistence";

export type ProfileCategory = "game" | "work" | "creator" | "daily";
export type ProfileSource = "factory" | "local" | "imported";

export interface ProfileLibraryEntry {
  id: string;
  name: string;
  category: ProfileCategory;
  revision: number;
  color: string;
  source: ProfileSource;
  createdAt: string;
  updatedAt: string;
  document: ProfileDocument;
}

export interface LocalSlotPlan {
  index: 0 | 1 | 2 | 3;
  profileId: string | null;
  readonly: boolean;
  pending: boolean;
}

interface PersistedLibrary {
  entries: ProfileLibraryEntry[];
  slots: LocalSlotPlan[];
  selectedProfileId: string;
  activeProfileId: string;
  selectedSlotIndex: 0 | 1 | 2 | 3;
}

const STORAGE_KEY = "kiiie.profile-library";
const STORAGE_VERSION = 1;
const FACTORY_ID = "factory_default";
const PROFILE_COLORS = ["#526bff", "#ff715b", "#42b99a", "#d99b3d", "#9a69df", "#3f98d7"];

function factoryDocument(): ProfileDocument {
  return normalizeProfileDocument(structuredClone(factoryProfileJson) as unknown as ProfileDocument);
}

function categoryFor(value: unknown): ProfileCategory {
  return value === "game" || value === "work" || value === "creator" || value === "daily"
    ? value
    : "daily";
}

function isPersistedLibrary(value: unknown): value is PersistedLibrary {
  if (typeof value !== "object" || value === null) return false;
  const data = value as Partial<PersistedLibrary>;
  return Array.isArray(data.entries)
    && data.entries.every((entry) => typeof entry === "object" && entry !== null && isProfileDocument((entry as ProfileLibraryEntry).document))
    && Array.isArray(data.slots)
    && typeof data.selectedProfileId === "string"
    && typeof data.activeProfileId === "string"
    && typeof data.selectedSlotIndex === "number";
}

function makeFactoryEntry(): ProfileLibraryEntry {
  const document = factoryDocument();
  return {
    id: FACTORY_ID,
    name: document.identity.name,
    category: "daily",
    revision: document.identity.revision,
    color: PROFILE_COLORS[0],
    source: "factory",
    createdAt: "",
    updatedAt: "",
    document,
  };
}

function defaultSlots(): LocalSlotPlan[] {
  return [
    { index: 0, profileId: FACTORY_ID, readonly: true, pending: false },
    { index: 1, profileId: null, readonly: false, pending: false },
    { index: 2, profileId: null, readonly: false, pending: false },
    { index: 3, profileId: null, readonly: false, pending: false },
  ];
}

function safeId(value: string): string {
  const normalized = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 54);
  return normalized || "profile";
}

export const useProfileLibraryStore = defineStore("profile-library", () => {
  const restoredEnvelope = loadLocalValue(STORAGE_KEY, STORAGE_VERSION, isPersistedLibrary);
  const restored = restoredEnvelope?.data;
  const factory = makeFactoryEntry();
  const restoredLocals = restored?.entries
    .filter((entry) => entry.source !== "factory" && entry.id !== FACTORY_ID)
    .map((entry) => ({ ...entry, document: normalizeProfileDocument(entry.document) })) ?? [];
  const entries = ref<ProfileLibraryEntry[]>([factory, ...restoredLocals]);
  const validIds = new Set(entries.value.map((entry) => entry.id));
  const slots = ref<LocalSlotPlan[]>(defaultSlots().map((fallback) => {
    const restoredSlot = restored?.slots.find((slot) => slot.index === fallback.index);
    if (!restoredSlot) return fallback;
    return {
      ...fallback,
      pending: fallback.index === 0 ? false : Boolean(restoredSlot.pending),
      profileId: fallback.index === 0
        ? FACTORY_ID
        : restoredSlot.profileId && validIds.has(restoredSlot.profileId) ? restoredSlot.profileId : null,
    };
  }));
  const selectedProfileId = ref(
    restored?.selectedProfileId && validIds.has(restored.selectedProfileId)
      ? restored.selectedProfileId
      : FACTORY_ID,
  );
  const activeProfileId = ref(
    restored?.activeProfileId && validIds.has(restored.activeProfileId)
      ? restored.activeProfileId
      : FACTORY_ID,
  );
  const selectedSlotIndex = ref<0 | 1 | 2 | 3>(
    restored?.selectedSlotIndex === 0
      || restored?.selectedSlotIndex === 1
      || restored?.selectedSlotIndex === 2
      || restored?.selectedSlotIndex === 3
      ? restored.selectedSlotIndex
      : 1,
  );
  const persistenceError = ref<string | null>(null);
  const lastSavedAt = ref<string | null>(restoredEnvelope?.savedAt ?? null);

  const selectedEntry = computed(() =>
    entries.value.find((entry) => entry.id === selectedProfileId.value) ?? entries.value[0],
  );
  const activeEntry = computed(() =>
    entries.value.find((entry) => entry.id === activeProfileId.value) ?? entries.value[0],
  );
  const selectedSlot = computed(() =>
    slots.value.find((slot) => slot.index === selectedSlotIndex.value) ?? slots.value[1],
  );
  const localEntries = computed(() => entries.value.filter((entry) => entry.source !== "factory"));

  function uniqueId(preferred: string): string {
    const base = safeId(preferred);
    let candidate = base;
    let suffix = 2;
    while (entries.value.some((entry) => entry.id === candidate) || candidate === FACTORY_ID) {
      candidate = `${base}_${suffix}`;
      suffix += 1;
    }
    return candidate;
  }

  function uniqueName(preferred: string): string {
    const base = preferred.trim() || "未命名 Profile";
    let candidate = base;
    let suffix = 2;
    while (entries.value.some((entry) => entry.name === candidate)) {
      candidate = `${base} ${suffix}`;
      suffix += 1;
    }
    return candidate;
  }

  function createFromDocument(
    source: ProfileDocument,
    name = "未命名 Profile",
    origin: Exclude<ProfileSource, "factory"> = "local",
  ): ProfileLibraryEntry {
    const document = normalizeProfileDocument(source);
    const displayName = uniqueName(name);
    const id = uniqueId(document.identity.profile_id || displayName);
    const now = new Date().toISOString();
    document.identity = {
      ...document.identity,
      profile_id: id,
      name: displayName,
      category: categoryFor(document.identity.category),
      revision: 1,
    };
    const entry: ProfileLibraryEntry = {
      id,
      name: displayName,
      category: categoryFor(document.identity.category),
      revision: 1,
      color: PROFILE_COLORS[entries.value.length % PROFILE_COLORS.length],
      source: origin,
      createdAt: now,
      updatedAt: now,
      document,
    };
    entries.value.push(entry);
    selectedProfileId.value = entry.id;
    return entry;
  }

  function createBlank(name = "未命名 Profile"): ProfileLibraryEntry {
    return createFromDocument(factory.document, name, "local");
  }

  function duplicate(entryId: string): ProfileLibraryEntry | null {
    const source = entries.value.find((entry) => entry.id === entryId);
    if (!source) return null;
    return createFromDocument(source.document, `${source.name} 副本`, "local");
  }

  function importDocument(document: ProfileDocument): ProfileLibraryEntry {
    if (!isProfileDocument(document)) throw new Error("文件不是有效的 KIIIe Profile JSON");
    const name = `${document.identity.name || "Imported Profile"}（导入）`;
    return createFromDocument(document, name, "imported");
  }

  function updateDocument(entryId: string, document: ProfileDocument): boolean {
    const entry = entries.value.find((candidate) => candidate.id === entryId);
    if (!entry || entry.source === "factory") return false;
    const copy = normalizeProfileDocument(document);
    copy.identity = {
      ...copy.identity,
      profile_id: entry.id,
      name: entry.name,
      category: entry.category,
      revision: Math.max(entry.revision + 1, Number(copy.identity.revision) || 1),
    };
    entry.document = copy;
    entry.revision = copy.identity.revision;
    entry.updatedAt = new Date().toISOString();
    return true;
  }

  function rename(entryId: string, name: string): boolean {
    const entry = entries.value.find((candidate) => candidate.id === entryId);
    const trimmed = name.trim();
    if (!entry || entry.source === "factory" || !trimmed) return false;
    entry.name = trimmed;
    entry.document.identity.name = trimmed;
    entry.updatedAt = new Date().toISOString();
    return true;
  }

  function remove(entryId: string): boolean {
    const index = entries.value.findIndex((entry) => entry.id === entryId);
    if (index < 0 || entries.value[index].source === "factory") return false;
    entries.value.splice(index, 1);
    slots.value.forEach((slot) => {
      if (slot.profileId === entryId) {
        slot.profileId = null;
        slot.pending = true;
      }
    });
    if (selectedProfileId.value === entryId) selectedProfileId.value = FACTORY_ID;
    if (activeProfileId.value === entryId) activeProfileId.value = FACTORY_ID;
    return true;
  }

  function assignToSlot(slotIndex: 1 | 2 | 3, profileId: string | null): boolean {
    const slot = slots.value.find((candidate) => candidate.index === slotIndex);
    const profile = profileId === null
      ? null
      : entries.value.find((entry) => entry.id === profileId);
    if (!slot || (profileId !== null && (!profile || profile.source === "factory"))) return false;
    slot.profileId = profileId;
    slot.pending = true;
    return true;
  }

  watch(
    [entries, slots, selectedProfileId, activeProfileId, selectedSlotIndex],
    () => {
      const savedAt = saveLocalValue<PersistedLibrary>(STORAGE_KEY, STORAGE_VERSION, {
        entries: entries.value.filter((entry) => entry.source !== "factory"),
        slots: slots.value,
        selectedProfileId: selectedProfileId.value,
        activeProfileId: activeProfileId.value,
        selectedSlotIndex: selectedSlotIndex.value,
      });
      if (savedAt) {
        lastSavedAt.value = savedAt;
        persistenceError.value = null;
      } else {
        persistenceError.value = "无法写入本地 Profile 库；请先导出 JSON，避免关闭后丢失";
      }
    },
    { deep: true },
  );

  return {
    entries,
    slots,
    selectedProfileId,
    activeProfileId,
    selectedSlotIndex,
    persistenceError,
    lastSavedAt,
    selectedEntry,
    activeEntry,
    selectedSlot,
    localEntries,
    createBlank,
    createFromDocument,
    duplicate,
    importDocument,
    updateDocument,
    rename,
    remove,
    assignToSlot,
  };
});
