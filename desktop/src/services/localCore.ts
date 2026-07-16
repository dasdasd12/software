import { invoke } from "@tauri-apps/api/core";
import { runningInTauri } from "./bridge";

export type LocalCoreProcessState =
  | "starting"
  | "running"
  | "external"
  | "stopped"
  | "unavailable"
  | "error";

export interface LocalCoreProcessStatus {
  state: LocalCoreProcessState;
  managed: boolean;
  pid?: number | null;
  url: string;
  backend?: string | null;
  lastError?: string | null;
  stderrTail: string[];
}

const BROWSER_STATUS: LocalCoreProcessStatus = {
  state: "external",
  managed: false,
  pid: null,
  url: "ws://127.0.0.1:8765",
  backend: "browser preview",
  lastError: null,
  stderrTail: [],
};

export async function startLocalCoreMonitor(): Promise<LocalCoreProcessStatus> {
  if (!runningInTauri()) return { ...BROWSER_STATUS };
  return invoke<LocalCoreProcessStatus>("local_core_start");
}

export async function readLocalCoreStatus(): Promise<LocalCoreProcessStatus> {
  if (!runningInTauri()) return { ...BROWSER_STATUS };
  return invoke<LocalCoreProcessStatus>("local_core_status");
}
