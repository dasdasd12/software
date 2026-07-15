import { onBeforeUnmount, onMounted } from "vue";

interface UnsavedChangesGuard {
  isDirty: () => boolean;
  message: string;
}

const guards = new Map<symbol, UnsavedChangesGuard>();

export function activeUnsavedChangeMessages(): string[] {
  const messages = [...guards.values()]
    .filter((guard) => guard.isDirty())
    .map((guard) => guard.message);
  return [...new Set(messages)];
}

export function hasUnsavedChanges(): boolean {
  return [...guards.values()].some((guard) => guard.isDirty());
}

export function useUnsavedChangesGuard(isDirty: () => boolean, message: string): void {
  const id = Symbol("unsaved-changes-guard");

  onMounted(() => guards.set(id, { isDirty, message }));
  onBeforeUnmount(() => guards.delete(id));
}
