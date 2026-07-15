import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import type { BridgeResponse, ProfileDocument } from "../domain/profile/types";

type JsonObject = Record<string, unknown>;

export interface BridgeEvent<T = JsonObject> {
  event: string;
  request_id: string;
  data: T;
}

export interface BridgeStatusEvent {
  state: string;
  message?: string;
}

interface BridgeRequestOptions {
  onEvent?: (event: BridgeEvent) => void;
}

export class BridgeClientError extends Error {
  readonly code: string;
  readonly recoverable: boolean;
  readonly details?: unknown;

  constructor(code: string, message: string, recoverable = true, details?: unknown) {
    super(message);
    this.name = "BridgeClientError";
    this.code = code;
    this.recoverable = recoverable;
    this.details = details;
  }
}

function normalizeBridgeError(error: unknown): BridgeClientError {
  if (error instanceof BridgeClientError) return error;
  if (error instanceof Error) {
    return new BridgeClientError(error.name || "bridge_error", error.message, true);
  }
  if (typeof error === "object" && error !== null) {
    const payload = error as Record<string, unknown>;
    return new BridgeClientError(
      typeof payload.code === "string" ? payload.code : "bridge_error",
      typeof payload.message === "string" ? payload.message : "桌面 Core 请求失败",
      typeof payload.recoverable === "boolean" ? payload.recoverable : true,
      payload.details,
    );
  }
  return new BridgeClientError("bridge_error", String(error || "桌面 Core 请求失败"), true);
}

function runningInTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

function makeRequestId(): string {
  return typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `req-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function browserPreviewRequest<T>(method: string, params: JsonObject): Promise<T> {
  if (method === "bridge.hello") {
    return {
      bridgeVersion: "browser-preview",
      protocolVersion: 1,
      methods: ["bridge.hello", "profile.compile"],
      connected: false,
      port: null,
    } as T;
  }
  if (method === "profile.compile") {
    const profile = params.profile as ProfileDocument;
    const bytes = new TextEncoder().encode(JSON.stringify(profile)).byteLength;
    return {
      preview: true,
      packageSize: bytes,
      sourceSize: bytes,
      profileId: profile.identity.profile_id,
      revision: profile.identity.revision,
      warnings: [],
    } as T;
  }
  throw new Error(`桌面 Core 仅在 Tauri 应用中可用（${method}）`);
}

export async function bridgeRequest<T = unknown>(
  method: string,
  params: JsonObject = {},
  options: BridgeRequestOptions = {},
): Promise<T> {
  if (!runningInTauri()) return browserPreviewRequest<T>(method, params);

  const request = { id: makeRequestId(), method, params };
  let unlisten: UnlistenFn | undefined;
  if (options.onEvent) {
    unlisten = await listen<BridgeEvent>("bridge:event", ({ payload }) => {
      if (payload.request_id === request.id) options.onEvent?.(payload);
    });
  }

  try {
    const response = await invoke<BridgeResponse<T> | T>("bridge_request", { request });

    if (typeof response === "object" && response !== null && "ok" in response) {
      const envelope = response as BridgeResponse<T>;
      if (!envelope.ok) {
        throw new BridgeClientError(
          envelope.error?.code ?? "bridge_error",
          envelope.error?.message ?? "桌面 Core 请求失败",
          envelope.error?.recoverable ?? true,
          envelope.error?.details,
        );
      }
      return envelope.result as T;
    }
    return response as T;
  } catch (error) {
    throw normalizeBridgeError(error);
  } finally {
    unlisten?.();
  }
}

export async function listenBridgeStatus(
  handler: (event: BridgeStatusEvent) => void,
): Promise<UnlistenFn> {
  if (!runningInTauri()) return () => undefined;
  return listen<BridgeStatusEvent>("bridge:status", ({ payload }) => handler(payload));
}

export { normalizeBridgeError, runningInTauri };
