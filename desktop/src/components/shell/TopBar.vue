<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import {
  activeUnsavedChangeMessages,
  discardUnsavedChanges,
  hasUnsavedChanges,
} from "../../composables/useUnsavedChangesGuard";
import { useDeviceStore } from "../../stores/device";
import { useProfileStore } from "../../stores/profile";
import {
  useProfileLibraryStore,
  type ProfileLibraryEntry,
} from "../../stores/profileLibrary";

const profile = useProfileStore();
const device = useDeviceStore();
const library = useProfileLibraryStore();
const router = useRouter();
const profileMenu = ref<HTMLElement | null>(null);
const profileMenuOpen = ref(false);

const activeEntry = computed(() =>
  library.entries.find((entry) => entry.id === profile.sourceDocument.identity.profile_id)
    ?? library.activeEntry,
);

const activeSourceLabel = computed(() => {
  if (activeEntry.value.source === "factory") {
    return profile.isDirty ? "Factory 草稿 · 尚未另存" : "Factory 只读模板";
  }
  return `本地 Profile · r${activeEntry.value.revision}`;
});

const profileActionLabel = computed(() => {
  if (activeEntry.value.source === "factory") {
    return profile.isDirty ? "另存为新 Profile" : "复制后编辑";
  }
  return profile.isDirty ? "保存 Profile" : "已保存";
});

const profileActionDisabled = computed(() =>
  activeEntry.value.source !== "factory" && !profile.isDirty,
);

const saveLabel = computed(() => {
  if (library.persistenceError) return "本地 Profile 保存失败";
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
    if (activeEntry.value.source === "factory") {
      return sections.length === 1
        ? `${sections[0]}修改暂存在 Factory 草稿`
        : `${sections.length} 类修改暂存在 Factory 草稿`;
    }
    return sections.length === 1 ? `${sections[0]}有更改` : `${sections.length} 类本地更改`;
  }
  return activeEntry.value.source === "factory" ? "Factory 模板未修改" : "本地 Profile 已保存";
});

const writeLabel = computed(() => {
  if (profile.writeState === "writing") {
    const percent = profile.writeProgress?.percent;
    return typeof percent === "number" ? `正在写入 ${percent}%` : "正在写入";
  }
  if (profile.writeState === "partial") return "已写入，未完成激活";
  return `写入 Slot ${device.targetUserSlot}`;
});

function confirmDiscardCurrentDraft(nextName: string): boolean {
  const transientMessages = activeUnsavedChangeMessages();
  if (!profile.isDirty && transientMessages.length === 0) return true;
  const messages = [...transientMessages];
  if (profile.isDirty) {
    messages.unshift(activeEntry.value.source === "factory"
      ? "Factory 的派生草稿还没有另存为 Profile。"
      : `「${activeEntry.value.name}」还有未保存修改。`);
  }
  const accepted = window.confirm(
    `${messages.join("\n")}切换到「${nextName}」会放弃这些内容，是否继续？`,
  );
  if (accepted) discardUnsavedChanges();
  return accepted;
}

function openProfileEntry(entry: ProfileLibraryEntry): void {
  if (entry.id === activeEntry.value.id) {
    profileMenuOpen.value = false;
    return;
  }
  if (!confirmDiscardCurrentDraft(entry.name)) return;
  profile.loadDocument(entry.document);
  library.activeProfileId = entry.id;
  library.selectedProfileId = entry.id;
  profileMenuOpen.value = false;
}

function openCreatedEntry(entry: ProfileLibraryEntry): void {
  profile.loadDocument(entry.document);
  library.activeProfileId = entry.id;
  library.selectedProfileId = entry.id;
  profileMenuOpen.value = false;
}

function createLocalProfile(): void {
  if (!confirmDiscardCurrentDraft("新的本地 Profile")) return;
  const entry = library.createBlank(`未命名 Profile ${library.localEntries.length + 1}`);
  openCreatedEntry(entry);
}

function saveCurrentProfile(): void {
  if (hasUnsavedChanges()) {
    window.alert("当前页面还有尚未应用的编辑。请先应用或放弃这些修改，再保存 Profile。");
    return;
  }
  if (activeEntry.value.source === "factory") {
    const entry = library.createFromDocument(
      profile.draftDocument,
      `${profile.identity.name} 副本`,
      "local",
    );
    openCreatedEntry(entry);
    return;
  }
  if (!profile.isDirty) return;
  if (!library.updateDocument(activeEntry.value.id, profile.draftDocument)) return;
  const updated = library.entries.find((entry) => entry.id === activeEntry.value.id);
  if (updated) openCreatedEntry(updated);
}

function profileEntryStatus(entry: ProfileLibraryEntry): string {
  if (entry.id === activeEntry.value.id) {
    if (entry.source === "factory" && profile.isDirty) return "当前 · 草稿未另存";
    if (entry.source !== "factory" && profile.isDirty) return "当前 · 有修改";
    return "当前编辑";
  }
  if (entry.source === "factory") return "只读模板";
  return `本地 · r${entry.revision}`;
}

function handleDocumentPointerDown(event: PointerEvent): void {
  if (!profileMenuOpen.value || profileMenu.value?.contains(event.target as Node)) return;
  profileMenuOpen.value = false;
}

onMounted(() => document.addEventListener("pointerdown", handleDocumentPointerDown));
onBeforeUnmount(() => document.removeEventListener("pointerdown", handleDocumentPointerDown));

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
    <div ref="profileMenu" class="profile-switcher-wrap">
      <button
        class="profile-switcher"
        type="button"
        :aria-expanded="profileMenuOpen"
        aria-haspopup="menu"
        @click="profileMenuOpen = !profileMenuOpen"
      >
        <span class="profile-swatch" :style="{ background: activeEntry.color }"></span>
        <span><small>{{ activeSourceLabel }} · 点击切换</small><b>{{ activeEntry.name }}</b></span>
        <span class="chevron" :class="{ open: profileMenuOpen }">⌄</span>
      </button>
      <div v-if="profileMenuOpen" class="profile-quick-menu" role="menu" aria-label="切换 Profile">
        <header>
          <span>电脑中的 Profile</span>
          <b>{{ library.entries.length }} 份</b>
        </header>
        <div class="profile-quick-list">
          <button
            v-for="entry in library.entries"
            :key="entry.id"
            type="button"
            role="menuitem"
            :class="{ active: entry.id === activeEntry.id }"
            @click="openProfileEntry(entry)"
          >
            <i :style="{ background: entry.color }"></i>
            <span><b>{{ entry.name }}</b><small>{{ profileEntryStatus(entry) }}</small></span>
            <em>{{ entry.id === activeEntry.id ? "✓" : "→" }}</em>
          </button>
        </div>
        <footer>
          <button type="button" @click="createLocalProfile">＋ 新建本地 Profile</button>
          <button type="button" @click="profileMenuOpen = false; router.push('/profiles')">管理 Profile 库 →</button>
        </footer>
      </div>
    </div>
    <div class="topbar-actions">
      <span class="save-state" aria-live="polite" :class="[`is-${library.persistenceError ? 'error' : profile.compileState}`, { dirty: profile.isDirty }]">
        <i></i>{{ saveLabel }}
      </span>
      <button
        class="button"
        :class="profile.isDirty ? 'secondary' : 'quiet'"
        :disabled="profileActionDisabled"
        @click="saveCurrentProfile"
      >
        {{ profileActionLabel }}
      </button>
      <button class="button quiet" :disabled="profile.compileState === 'compiling'" @click="profile.validateDraft">
        验证 Profile
      </button>
      <button
        class="button primary"
        :disabled="!device.connected || profile.writeState === 'writing'"
        :title="device.connected ? `编译当前草稿并写入用户 Slot ${device.targetUserSlot}` : '连接设备后可写入'"
        @click="writeProfile"
      >
        <i class="write-dot"></i>{{ writeLabel }}
      </button>
      <button class="icon-button" aria-label="更多操作（待接入）" disabled title="更多操作待接入">•••</button>
    </div>
  </header>
</template>
