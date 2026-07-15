import { computed, ref } from "vue";
import { defineStore } from "pinia";
import { bridgeRequest, listenBridgeStatus, type BridgeStatusEvent } from "../services/bridge";

export interface SerialPortInfo {
  device: string;
  name?: string;
  description?: string;
  hwid?: string;
  vid?: number;
  pid?: number;
  serialNumber?: string;
}

export interface DeviceInfo {
  activeSlot?: number;
  profileId16?: number;
  generation?: number;
  slotValid?: boolean[];
}

interface ConnectResult {
  port: string;
  info?: DeviceInfo;
}

export const useDeviceStore = defineStore("device", () => {
  const state = ref<"disconnected" | "connecting" | "connected" | "error">("disconnected");
  const port = ref<string | null>(null);
  const ports = ref<SerialPortInfo[]>([]);
  const deviceActiveSlot = ref(0);
  // Slot 0 is the read-only factory profile. Editing always targets user slots 1-3.
  const targetUserSlot = ref(1);
  const slotCount = ref(3);
  const profileId16 = ref<number | null>(null);
  const generation = ref<number | null>(null);
  const slotValid = ref<boolean[]>([false, false, false]);
  const firmwareVersion = ref<string | null>(null);
  const lastError = ref<string | null>(null);

  const connected = computed(() => state.value === "connected");
  const statusLabel = computed(() => {
    if (state.value === "connecting") return "正在连接";
    if (state.value === "connected") return port.value ?? "已连接";
    if (state.value === "error") return "连接异常";
    return "等待连接";
  });

  let stopStatusListener: (() => void) | undefined;

  function applyDeviceInfo(info: DeviceInfo): void {
    const reportedSlot = Number(info.activeSlot ?? 0);
    deviceActiveSlot.value = reportedSlot >= 0 && reportedSlot <= 3 ? reportedSlot : 0;
    if (deviceActiveSlot.value >= 1) targetUserSlot.value = deviceActiveSlot.value;
    slotValid.value = info.slotValid?.slice(0, 3) ?? [false, false, false];
    slotCount.value = Math.max(1, slotValid.value.length || 3);
    profileId16.value = typeof info.profileId16 === "number" ? info.profileId16 : null;
    generation.value = typeof info.generation === "number" ? info.generation : null;
  }

  async function initializeBridgeEvents(): Promise<void> {
    if (stopStatusListener) return;
    try {
      stopStatusListener = await listenBridgeStatus((event: BridgeStatusEvent) => {
        if (event.state !== "disconnected") return;
        state.value = "error";
        port.value = null;
        deviceActiveSlot.value = 0;
        profileId16.value = null;
        generation.value = null;
        slotValid.value = [false, false, false];
        lastError.value = event.message ?? "桌面 Core 已断开";
      });
    } catch (error) {
      lastError.value = error instanceof Error ? error.message : String(error);
    }
  }

  function disposeBridgeEvents(): void {
    stopStatusListener?.();
    stopStatusListener = undefined;
  }

  async function refreshPorts(): Promise<void> {
    try {
      const result = await bridgeRequest<{ ports: SerialPortInfo[] } | SerialPortInfo[]>("device.list_ports");
      ports.value = Array.isArray(result) ? result : result.ports;
    } catch (error) {
      lastError.value = error instanceof Error ? error.message : String(error);
    }
  }

  async function connect(selectedPort: string): Promise<void> {
    state.value = "connecting";
    lastError.value = null;
    try {
      const result = await bridgeRequest<ConnectResult>("device.connect", { port: selectedPort });
      const info = result.info ?? {};
      port.value = result.port || selectedPort;
      applyDeviceInfo(info);
      firmwareVersion.value = null;
      state.value = "connected";
    } catch (error) {
      state.value = "error";
      lastError.value = error instanceof Error ? error.message : String(error);
    }
  }

  async function disconnect(): Promise<void> {
    try {
      await bridgeRequest("device.disconnect");
    } finally {
      state.value = "disconnected";
      port.value = null;
      deviceActiveSlot.value = 0;
      profileId16.value = null;
      generation.value = null;
      slotValid.value = [false, false, false];
    }
  }

  return {
    state,
    port,
    ports,
    deviceActiveSlot,
    targetUserSlot,
    slotCount,
    profileId16,
    generation,
    slotValid,
    firmwareVersion,
    lastError,
    connected,
    statusLabel,
    applyDeviceInfo,
    initializeBridgeEvents,
    disposeBridgeEvents,
    refreshPorts,
    connect,
    disconnect,
  };
});
