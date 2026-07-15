export interface StoredEnvelope<T> {
  version: number;
  savedAt: string;
  data: T;
}

export interface LoadedLocalValue<T> {
  data: T;
  savedAt: string;
}

function storageAvailable(): boolean {
  return typeof window !== "undefined" && typeof window.localStorage !== "undefined";
}

export function loadLocalValue<T>(
  key: string,
  version: number,
  isValid?: (value: unknown) => value is T,
): LoadedLocalValue<T> | null {
  if (!storageAvailable()) return null;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const envelope = JSON.parse(raw) as Partial<StoredEnvelope<unknown>>;
    if (envelope.version !== version || typeof envelope.savedAt !== "string") return null;
    if (isValid && !isValid(envelope.data)) return null;
    return { data: envelope.data as T, savedAt: envelope.savedAt };
  } catch {
    return null;
  }
}

export function saveLocalValue<T>(key: string, version: number, data: T): string | null {
  if (!storageAvailable()) return null;
  const savedAt = new Date().toISOString();
  const envelope: StoredEnvelope<T> = { version, savedAt, data };
  try {
    window.localStorage.setItem(key, JSON.stringify(envelope));
    return savedAt;
  } catch {
    return null;
  }
}

export function removeLocalValue(key: string): void {
  if (!storageAvailable()) return;
  try {
    window.localStorage.removeItem(key);
  } catch {
    // Storage can be unavailable in hardened WebViews. The in-memory draft remains usable.
  }
}

export async function readJsonFile<T>(file: File): Promise<T> {
  return JSON.parse(await file.text()) as T;
}

export function downloadJson(filename: string, value: unknown): void {
  if (typeof document === "undefined") return;
  const blob = new Blob([`${JSON.stringify(value, null, 2)}\n`], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.hidden = true;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function formatSavedTime(isoTime: string | null): string {
  if (!isoTime) return "尚未保存";
  const date = new Date(isoTime);
  if (Number.isNaN(date.getTime())) return "已保存";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}
