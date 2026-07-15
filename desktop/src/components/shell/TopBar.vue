<script setup lang="ts">
import { computed } from "vue";
import { useDeviceStore } from "../../stores/device";
import { useProfileStore } from "../../stores/profile";

const profile = useProfileStore();
const device = useDeviceStore();

const saveLabel = computed(() => {
  if (profile.compileState === "compiling") return "正在验证";
  if (profile.compileState === "error") return "验证失败";
  if (profile.isDirty) {
    const labels: Record<string, string> = {
      identity: "Profile 信息",
      bindings: "键位",
      trigger: "触发参数",
      behaviors: "高级行为",
      interactions: "交互规则",
      macros: "宏",
      "report-rate": "回报率",
      "input-guard": "输入保护",
      other: "高级字段",
    };
    const sections = profile.dirtySections.map((section) => labels[section] ?? section);
    return sections.length === 1 ? `${sections[0]}有更改` : `${sections.length} 类本地更改`;
  }
  return "恢复草稿已保存";
});

const writeLabel = computed(() => {
  if (profile.writeState === "writing") {
    const percent = profile.writeProgress?.percent;
    return typeof percent === "number" ? `正在写入 ${percent}%` : "正在写入";
  }
  if (profile.writeState === "partial") return "已写入，未完成激活";
  return "写入设备";
});

async function writeProfile(): Promise<void> {
  if (!device.connected || profile.writeState === "writing") return;
  try {
    const result = await profile.installToDevice(device.targetUserSlot, true);
    device.applyDeviceInfo(result.info);
  } catch {
    // The store exposes the structured Core error in its status message.
  }
}
</script>

<template>
  <header class="topbar">
    <RouterLink class="profile-switcher" to="/profiles">
      <span class="profile-swatch"></span>
      <span><small>当前 Profile</small><b>{{ profile.identity.name }}</b></span>
      <span class="chevron">⌄</span>
    </RouterLink>
    <div class="topbar-actions">
      <span class="save-state" aria-live="polite" :class="[`is-${profile.compileState}`, { dirty: profile.isDirty }]">
        <i></i>{{ saveLabel }}
      </span>
      <button class="button quiet" :disabled="profile.compileState === 'compiling'" @click="profile.validateDraft">
        验证 Profile
      </button>
      <button
        class="button primary"
        :disabled="!device.connected || profile.writeState === 'writing'"
        :title="device.connected ? '编译并写入当前设备 Slot' : '连接设备后可写入'"
        @click="writeProfile"
      >
        <i class="write-dot"></i>{{ writeLabel }}
      </button>
      <button class="icon-button" aria-label="更多操作（待接入）" disabled title="更多操作待接入">•••</button>
    </div>
  </header>
</template>
