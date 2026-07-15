<script setup lang="ts">
import { onBeforeUnmount, onMounted, watchEffect } from "vue";
import { isTauri } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { confirm } from "@tauri-apps/plugin-dialog";
import { useRoute } from "vue-router";
import AppShell from "./components/shell/AppShell.vue";
import { activeUnsavedChangeMessages, hasUnsavedChanges } from "./composables/useUnsavedChangesGuard";
import { useDeviceStore } from "./stores/device";
import { useProfileStore } from "./stores/profile";

const route = useRoute();
const device = useDeviceStore();
const profile = useProfileStore();

let disposed = false;
let nativeCloseApproved = false;
let unlistenCloseRequested: (() => void) | null = null;

function handleBeforeUnload(event: BeforeUnloadEvent): void {
  profile.persistWorkspace();
  if (nativeCloseApproved || !hasUnsavedChanges()) return;
  event.preventDefault();
  event.returnValue = "";
}

async function initializeApp(): Promise<void> {
  void device.initializeBridgeEvents();
  window.addEventListener("beforeunload", handleBeforeUnload);
  if (!isTauri()) return;

  try {
    const unlisten = await getCurrentWindow().onCloseRequested(async (event) => {
      profile.persistWorkspace();
      const messages = activeUnsavedChangeMessages();
      if (messages.length === 0) return;

      try {
        const details = messages.length === 1
          ? messages[0]
          : `以下编辑尚未应用：\n${messages.map((message) => `- ${message}`).join("\n")}`;
        const shouldClose = await confirm(
          `${details}\n\n关闭应用会放弃这些内容，是否继续？`,
          {
            title: "KIIIe Control Lab",
            kind: "warning",
            okLabel: "放弃并关闭",
            cancelLabel: "继续编辑",
          },
        );
        if (!shouldClose) {
          event.preventDefault();
          return;
        }

        nativeCloseApproved = true;
        window.setTimeout(() => {
          nativeCloseApproved = false;
        }, 1_000);
      } catch (error) {
        event.preventDefault();
        console.error("无法确认窗口关闭请求", error);
      }
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
