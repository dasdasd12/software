<script setup lang="ts">
import { onBeforeUnmount, onMounted, watch, watchEffect } from "vue";
import { isTauri } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { useRoute } from "vue-router";
import AppShell from "./components/shell/AppShell.vue";
import { hasUnsavedChanges } from "./composables/useUnsavedChangesGuard";
import { useAiMonitorStore } from "./stores/aiMonitor";
import { useDeviceStore } from "./stores/device";
import { useProfileStore } from "./stores/profile";
import { useProfileLibraryStore } from "./stores/profileLibrary";

const route = useRoute();
const aiMonitor = useAiMonitorStore();
const device = useDeviceStore();
const profile = useProfileStore();
const profileLibrary = useProfileLibraryStore();

let disposed = false;
let nativeCloseApproved = false;
let unlistenCloseRequested: (() => void) | null = null;

watch(
  () => profile.sourceDocument.identity.profile_id,
  (profileId) => {
    if (!profileLibrary.entries.some((entry) => entry.id === profileId)) return;
    profileLibrary.activeProfileId = profileId;
  },
  { immediate: true },
);

function handleBeforeUnload(event: BeforeUnloadEvent): void {
  profile.persistWorkspace();
  if (nativeCloseApproved || !hasUnsavedChanges()) return;
  event.preventDefault();
  event.returnValue = "";
}

async function initializeApp(): Promise<void> {
  void device.initializeBridgeEvents();
  void aiMonitor.initialize();
  window.addEventListener("beforeunload", handleBeforeUnload);
  if (!isTauri()) return;

  try {
    const unlisten = await getCurrentWindow().onCloseRequested(() => {
      profile.persistWorkspace();
      // Native close must remain deterministic. Page-level navigation and browser
      // refresh still protect unapplied buffers, while the desktop title-bar close
      // flushes the recoverable Profile workspace and exits without a hidden modal.
      nativeCloseApproved = true;
      window.setTimeout(() => {
        nativeCloseApproved = false;
      }, 2_000);
    });
    if (disposed) unlisten();
    else unlistenCloseRequested = unlisten;
  } catch (error) {
    console.error("无法注册窗口关闭保护", error);
  }
}

onMounted(() => void initializeApp());
onBeforeUnmount(() => {
  disposed = true;
  window.removeEventListener("beforeunload", handleBeforeUnload);
  unlistenCloseRequested?.();
  aiMonitor.dispose();
  device.disposeBridgeEvents();
});

watchEffect(() => {
  document.documentElement.dataset.inspector = route.meta.inspector === false ? "closed" : "open";
  document.documentElement.dataset.layout = String(route.meta.layout ?? "canvas");
});
</script>

<template>
  <AppShell />
</template>
