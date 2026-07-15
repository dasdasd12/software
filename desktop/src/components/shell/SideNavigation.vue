<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import { useDeviceStore } from "../../stores/device";

interface NavItem {
  label: string;
  route: string;
  glyph: string;
  badge?: string;
  disabled?: boolean;
}

const route = useRoute();
const device = useDeviceStore();

const sections: Array<{ label: string; items: NavItem[] }> = [
  {
    label: "键盘行为",
    items: [
      { label: "键位与触发", route: "/keymap", glyph: "glyph-keys" },
      { label: "高级行为", route: "/behavior", glyph: "glyph-flow" },
      { label: "宏与规则", route: "/macro", glyph: "glyph-macro" },
    ],
  },
  {
    label: "独立资源",
    items: [
      { label: "灯效", route: "/lighting", glyph: "glyph-light" },
      { label: "屏显", route: "/display", glyph: "glyph-screen" },
      { label: "AI 控制", route: "", glyph: "glyph-ai", badge: "暂缓", disabled: true },
    ],
  },
  {
    label: "设备",
    items: [
      { label: "Profile 库", route: "/profiles", glyph: "glyph-profile" },
      { label: "校准与诊断", route: "/diagnostics", glyph: "glyph-pulse" },
      { label: "设备与连接", route: "/device", glyph: "glyph-device" },
    ],
  },
];

const deviceMeta = computed(() => {
  if (!device.connected) return "尚未建立 USB 连接";
  const activeProfile = device.deviceActiveSlot === 0 ? "Factory" : `Slot ${device.deviceActiveSlot}`;
  return `${device.port ?? "USB"} · ${activeProfile}`;
});
</script>

<template>
  <aside class="sidebar" aria-label="主导航">
    <div class="brand-lockup">
      <span class="brand-mark" aria-hidden="true"><i></i><b></b></span>
      <span><strong>KIIIe</strong><small>CONTROL LAB</small></span>
    </div>

    <nav class="primary-nav">
      <template v-for="(section, sectionIndex) in sections" :key="section.label">
        <p class="nav-label" :class="{ 'nav-gap': sectionIndex > 0 }">{{ section.label }}</p>
        <RouterLink
          v-for="item in section.items"
          :key="item.label"
          :to="item.disabled ? route.fullPath : item.route"
          class="nav-item"
          :class="{ active: !item.disabled && route.path === item.route, disabled: item.disabled }"
          :aria-disabled="item.disabled"
          :tabindex="item.disabled ? -1 : 0"
          @click="item.disabled && $event.preventDefault()"
        >
          <i class="nav-glyph" :class="item.glyph">{{ item.glyph === 'glyph-ai' ? 'AI' : '' }}</i>
          <span>{{ item.label }}</span>
          <em v-if="item.badge" class="nav-badge">{{ item.badge }}</em>
        </RouterLink>
      </template>
    </nav>

    <footer class="sidebar-footer">
      <div class="device-presence" :class="{ offline: !device.connected }">
        <span class="presence-dot"></span>
        <span><b>AK Ergo 77</b><small>{{ device.statusLabel }}</small></span>
        <strong>{{ device.connected ? 'USB' : '—' }}</strong>
      </div>
      <div class="firmware-meta">
        <span>{{ device.firmwareVersion ? `FW ${device.firmwareVersion}` : 'FW —' }}</span>
        <span>{{ deviceMeta }}</span>
      </div>
    </footer>
  </aside>
</template>
