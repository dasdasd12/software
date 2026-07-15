import { computed, ref } from "vue";
import { defineStore } from "pinia";
import { KEYBOARD_KEYS } from "../domain/keyboard/layout";

const GROUPS: Record<string, string[]> = {
  主键: KEYBOARD_KEYS.map((key) => key.id),
  WASD: ["key_058", "key_065", "key_064", "key_063"],
  左半区: KEYBOARD_KEYS.filter((key) => key.side === "left").map((key) => key.id),
  右半区: KEYBOARD_KEYS.filter((key) => key.side === "right").map((key) => key.id),
};

export const useEditorStore = defineStore("editor", () => {
  const selectedControlIds = ref<string[]>([]);
  const lastKeyId = ref<string | null>(null);

  const primaryControlId = computed(() =>
    selectedControlIds.value[selectedControlIds.value.length - 1] ?? "",
  );
  const selectionCount = computed(() => selectedControlIds.value.length);

  function selectControl(controlId: string, additive = false, range = false): void {
    if (range && controlId.startsWith("key_") && lastKeyId.value?.startsWith("key_")) {
      const from = KEYBOARD_KEYS.findIndex((key) => key.id === lastKeyId.value);
      const to = KEYBOARD_KEYS.findIndex((key) => key.id === controlId);
      if (from >= 0 && to >= 0) {
        const [start, end] = from < to ? [from, to] : [to, from];
        const rangeIds = KEYBOARD_KEYS.slice(start, end + 1).map((key) => key.id);
        selectedControlIds.value = additive
          ? Array.from(new Set([...selectedControlIds.value, ...rangeIds]))
          : rangeIds;
      }
    } else if (additive) {
      selectedControlIds.value = selectedControlIds.value.includes(controlId)
        ? selectedControlIds.value.filter((id) => id !== controlId)
        : [...selectedControlIds.value, controlId];
    } else {
      selectedControlIds.value = [controlId];
    }
    if (controlId.startsWith("key_")) lastKeyId.value = controlId;
  }

  function selectGroup(group: string): void {
    const ids = GROUPS[group];
    if (!ids) return;
    selectedControlIds.value = [...ids];
    lastKeyId.value = ids[ids.length - 1] ?? null;
  }

  function clearSelection(): void {
    selectedControlIds.value = [];
    lastKeyId.value = null;
  }

  return {
    selectedControlIds,
    primaryControlId,
    selectionCount,
    selectControl,
    selectGroup,
    clearSelection,
  };
});
