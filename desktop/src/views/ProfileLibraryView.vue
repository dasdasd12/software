<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { storeToRefs } from "pinia";
import {
  activeUnsavedChangeMessages,
  discardUnsavedChanges,
  hasUnsavedChanges,
} from "../composables/useUnsavedChangesGuard";
import { isProfileDocument } from "../domain/profile/document";
import type { ProfileDocument } from "../domain/profile/types";
import { downloadJson, readJsonFile } from "../services/localPersistence";
import { useDeviceStore } from "../stores/device";
import { useProfileStore } from "../stores/profile";
import {
  useProfileLibraryStore,
  type LocalSlotPlan,
  type ProfileCategory,
  type ProfileLibraryEntry,
} from "../stores/profileLibrary";

type Category = "all" | ProfileCategory;
type InspectorTab = "details" | "diff" | "policy";

const profileStore = useProfileStore();
const library = useProfileLibraryStore();
const device = useDeviceStore();
const {
  entries: profiles,
  slots,
  selectedProfileId,
  activeProfileId,
  selectedSlotIndex,
  selectedEntry: selectedProfile,
  activeEntry,
  selectedSlot,
} = storeToRefs(library);

const importInput = ref<HTMLInputElement | null>(null);
const activeFilter = ref<Category>("all");
const inspectorTab = ref<InspectorTab>("details");
const localMessage = ref("本地库已启用；Slot 分配仍是电脑中的规划，不代表键盘当前状态");
const importError = ref("");

const filterOptions: Array<{ id: Category; label: string }> = [
  { id: "all", label: "全部" },
  { id: "game", label: "游戏" },
  { id: "work", label: "工作" },
  { id: "creator", label: "创作" },
  { id: "daily", label: "日常" },
];

const categoryLabels: Record<ProfileCategory, string> = {
  game: "Game",
  work: "Work",
  creator: "Creator",
  daily: "Daily",
};

const visibleProfiles = computed(() =>
  activeFilter.value === "all"
    ? profiles.value
    : profiles.value.filter((profile) => profile.category === activeFilter.value),
);
const usedSlotCount = computed(() => slots.value.filter((slot) => slot.index > 0 && slot.profileId !== null).length);
const selectedIsActive = computed(() => selectedProfile.value.id === activeProfileId.value);
const activeIsFactory = computed(() => activeEntry.value.source === "factory");
const factoryDraftPending = computed(() => activeIsFactory.value && profileStore.isDirty);
const selectedDocument = computed(() =>
  selectedIsActive.value ? profileStore.draftDocument : selectedProfile.value.document,
);
const activeWorkspaceStatus = computed(() => {
  if (factoryDraftPending.value) return "修改目前只在 Factory 恢复草稿中，尚未形成新的 Profile";
  if (activeIsFactory.value) return "当前打开的是只读 Factory 模板";
  if (profileStore.isDirty) return `「${activeEntry.value.name}」有未保存的工作草稿`;
  return `「${activeEntry.value.name}」已保存到电脑`;
});
const macroCount = computed(() => Object.keys(selectedDocument.value.macro_defs ?? {}).length);
const canAssignSelectedProfile = computed(() =>
  !selectedSlot.value.readonly
  && selectedProfile.value.source !== "factory"
  && !(selectedIsActive.value && profileStore.isDirty),
);
const assignActionLabel = computed(() => {
  if (selectedProfile.value.source === "factory") return "先复制 Factory";
  if (selectedIsActive.value && profileStore.isDirty) return "先保存当前 Profile";
  return "分配所选 Profile →";
});

watch(
  selectedSlotIndex,
  (slotIndex) => {
    if (slotIndex > 0) device.targetUserSlot = slotIndex;
  },
  { immediate: true },
);
const primaryActionLabel = computed(() => {
  if (!selectedIsActive.value) return "打开此 Profile";
  if (selectedProfile.value.source === "factory") return profileStore.isDirty ? "另存为本地副本" : "复制后编辑";
  return profileStore.isDirty ? "保存当前草稿" : "当前已打开";
});

function categoryLabel(profile: ProfileLibraryEntry): string {
  return categoryLabels[profile.category];
}

function profileEntryStatus(entry: ProfileLibraryEntry): string {
  if (entry.id === activeProfileId.value) {
    if (entry.source === "factory" && profileStore.isDirty) return "当前 · 草稿未另存";
    if (entry.source !== "factory" && profileStore.isDirty) return "当前 · 有修改";
    return "当前编辑";
  }
  if (entry.source === "factory") return "只读模板";
  if (entry.source === "imported") return "已导入";
  return "仅本地";
}

function profileForSlot(slot: LocalSlotPlan): ProfileLibraryEntry | undefined {
  return profiles.value.find((profile) => profile.id === slot.profileId);
}

function selectProfile(profile: ProfileLibraryEntry): void {
  selectedProfileId.value = profile.id;
  importError.value = "";
  localMessage.value = `已选择本地 Profile「${profile.name}」`;
}

function selectSlot(slot: LocalSlotPlan): void {
  selectedSlotIndex.value = slot.index;
  inspectorTab.value = "details";
  if (slot.index > 0) device.targetUserSlot = slot.index;
  const profile = profileForSlot(slot);
  if (profile) selectedProfileId.value = profile.id;
  localMessage.value = slot.readonly
    ? "Slot 0 是只读 Factory 恢复副本，不能覆盖或删除"
    : `已选择 Slot ${slot.index} 的本地分配草稿`;
}

function confirmDiscardActiveDraft(nextName: string): boolean {
  const transientMessages = activeUnsavedChangeMessages();
  if (!profileStore.isDirty && transientMessages.length === 0) return true;
  const messages = [...transientMessages];
  if (profileStore.isDirty) {
    messages.unshift(activeIsFactory.value
      ? "Factory 的派生草稿还没有另存为 Profile。"
      : `「${activeEntry.value.name}」还有未保存修改。`);
  }
  const accepted = window.confirm(
    `${messages.join("\n")}切换到「${nextName}」会放弃这些内容，是否继续？`,
  );
  if (accepted) discardUnsavedChanges();
  return accepted;
}

function openProfile(profile: ProfileLibraryEntry, skipDirtyCheck = false): boolean {
  if (!skipDirtyCheck
    && profile.id !== activeProfileId.value
    && !confirmDiscardActiveDraft(profile.name)) return false;
  profileStore.loadDocument(profile.document);
  activeProfileId.value = profile.id;
  selectedProfileId.value = profile.id;
  localMessage.value = `已打开「${profile.name}」；编辑器已切换到这份本地来源`;
  return true;
}

function assignSelectedProfile(): void {
  if (selectedSlot.value.readonly) return;
  if (selectedProfile.value.source === "factory") {
    localMessage.value = "Factory 是只读模板；请先复制为本地 Profile，再分配到用户 Slot";
    return;
  }
  if (selectedIsActive.value && profileStore.isDirty) {
    localMessage.value = "当前工作草稿尚未保存；请先保存 Profile，再分配到 Slot";
    return;
  }
  library.assignToSlot(selectedSlot.value.index as 1 | 2 | 3, selectedProfile.value.id);
  localMessage.value = `${selectedProfile.value.name} 已分配到 Slot ${selectedSlot.value.index} 的本地计划；尚未写入设备`;
}

function clearSelectedSlot(): void {
  if (selectedSlot.value.readonly) return;
  library.assignToSlot(selectedSlot.value.index as 1 | 2 | 3, null);
  localMessage.value = `Slot ${selectedSlot.value.index} 已在本地计划中清空；尚未写入设备`;
}

function saveFactoryDraft(): void {
  if (hasUnsavedChanges()) {
    localMessage.value = "当前页面还有尚未应用的编辑；请先应用或放弃，再另存 Profile";
    return;
  }
  const copy = library.createFromDocument(
    profileStore.draftDocument,
    `${profileStore.identity.name} 副本`,
    "local",
  );
  openProfile(copy, true);
  activeFilter.value = "all";
  localMessage.value = `Factory 保持只读；当前修改已保存为「${copy.name}」并切换到这份 Profile`;
}

function cloneFactory(): void {
  const selectedIsCurrent = selectedProfile.value.id === activeProfileId.value;
  if (!selectedIsCurrent && !confirmDiscardActiveDraft(`${selectedProfile.value.name} 副本`)) return;
  const source = selectedIsCurrent ? profileStore.draftDocument : selectedProfile.value.document;
  const copy = library.createFromDocument(source, `${selectedProfile.value.name} 副本`, "local");
  openProfile(copy, true);
  activeFilter.value = "all";
  localMessage.value = `已创建、保存并打开可编辑的本地 Profile「${copy.name}」`;
}

function createProfile(): void {
  const sequence = library.localEntries.length + 1;
  if (!confirmDiscardActiveDraft(`未命名 Profile ${sequence}`)) return;
  const entry = library.createBlank(`未命名 Profile ${sequence}`);
  openProfile(entry, true);
  activeFilter.value = "all";
  localMessage.value = "已创建并保存本地 Profile；后续修改会自动保留为恢复草稿";
}

function duplicateSelectedProfile(): void {
  const selectedIsCurrent = selectedProfile.value.id === activeProfileId.value;
  if (!selectedIsCurrent && !confirmDiscardActiveDraft(`${selectedProfile.value.name} 副本`)) return;
  const source = selectedIsCurrent ? profileStore.draftDocument : selectedProfile.value.document;
  const copy = library.createFromDocument(source, `${selectedProfile.value.name} 副本`, "local");
  openProfile(copy, true);
  activeFilter.value = "all";
  localMessage.value = `已复制并打开「${copy.name}」`;
}

function saveOrOpenSelected(): void {
  if (!selectedIsActive.value) {
    openProfile(selectedProfile.value);
    return;
  }
  if (selectedProfile.value.source === "factory") {
    saveFactoryDraft();
    return;
  }
  if (hasUnsavedChanges()) {
    localMessage.value = "当前页面还有尚未应用的编辑；请先应用或放弃，再保存 Profile";
    return;
  }
  if (!profileStore.isDirty) return;
  library.updateDocument(selectedProfile.value.id, profileStore.draftDocument);
  const updated = profiles.value.find((profile) => profile.id === selectedProfile.value.id);
  if (updated) profileStore.loadDocument(updated.document);
  localMessage.value = `「${selectedProfile.value.name}」已保存到电脑，本地修订为 r${selectedProfile.value.revision}`;
}

function exportSelectedProfile(): void {
  const exportingDraft = selectedIsActive.value && profileStore.isDirty;
  downloadJson(`${selectedProfile.value.id}${exportingDraft ? "-draft" : ""}.json`, selectedDocument.value);
  localMessage.value = exportingDraft
    ? `已导出「${selectedProfile.value.name}」的当前工作草稿`
    : `已导出「${selectedProfile.value.name}」的已保存版本`;
}

function openImportPicker(): void {
  importInput.value?.click();
}

async function importProfile(event: Event): Promise<void> {
  const input = event.currentTarget as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) {
    importError.value = "Profile JSON 不能超过 2 MB";
    return;
  }
  try {
    const document = await readJsonFile<ProfileDocument>(file);
    if (!isProfileDocument(document)) throw new Error("文件不是有效的 KIIIe Profile JSON");
    if (!confirmDiscardActiveDraft(`${document.identity.name || file.name}（导入）`)) {
      localMessage.value = `已取消导入 ${file.name}；当前工作草稿保持不变`;
      return;
    }
    const entry = library.importDocument(document);
    openProfile(entry, true);
    activeFilter.value = "all";
    importError.value = "";
    localMessage.value = `已导入并打开 ${file.name}；写入前可以先验证 Profile`;
  } catch (error) {
    importError.value = error instanceof Error ? error.message : String(error);
  }
}
</script>

<template>
  <div class="page-shell">
    <section class="page-main profiles-main">
      <header class="page-heading">
        <div>
          <p class="page-kicker">PROFILE LIBRARY / DEVICE STORE</p>
          <h1>Profile 库与设备 Slot</h1>
          <p class="lede">先在左侧选择 Profile，再从右侧打开编辑。Factory 是只读模板；本地 Profile 才是可持续保存和写入用户 Slot 的配置。</p>
        </div>
        <div class="heading-actions">
          <button class="button quiet" @click="exportSelectedProfile">导出 JSON</button>
          <button class="button quiet" @click="openImportPicker">导入 JSON</button>
          <button class="button primary" @click="createProfile">新建本地 Profile</button>
          <input ref="importInput" class="visually-hidden-input" type="file" accept="application/json,.json" @change="importProfile" />
        </div>
      </header>

      <section v-if="factoryDraftPending" class="profile-draft-banner" aria-live="polite">
        <i>草稿</i>
        <div>
          <b>你修改的是 Factory 派生草稿，还没有创建新的 Profile</b>
          <p>修改已进入恢复草稿，所以不会丢失；另存后才会出现在“我的 Profile”中，并成为当前编辑对象。</p>
        </div>
        <button class="button secondary" @click="saveFactoryDraft">另存为本地 Profile</button>
      </section>

      <section class="profile-workspace">
        <div class="library-pane">
          <header>
            <div><span class="tiny-label">PC LIBRARY · LOCAL DRAFT</span><h2>我的 Profile</h2></div>
            <button aria-label="导出所选 Profile" title="导出所选 Profile JSON" @click="exportSelectedProfile">↓</button>
          </header>
          <div class="library-filter">
            <button
              v-for="filter in filterOptions"
              :key="filter.id"
              class="status-link"
              style="padding: 0"
              @click="activeFilter = filter.id"
            ><span :class="{ active: activeFilter === filter.id }">{{ filter.label }} {{ filter.id === 'all' ? profiles.length : profiles.filter((profile) => profile.category === filter.id).length }}</span></button>
          </div>
          <div class="library-list">
            <article
              v-for="profile in visibleProfiles"
              :key="profile.id"
              role="button"
              tabindex="0"
              style="cursor: pointer"
              :class="{ active: profile.id === selectedProfileId }"
              @click="selectProfile(profile)"
              @keydown.enter="selectProfile(profile)"
              @keydown.space.prevent="selectProfile(profile)"
            >
              <i :style="{ background: profile.color }"></i>
              <span><b>{{ profile.name }}</b><small>{{ categoryLabel(profile) }} · r{{ profile.revision }}</small></span>
              <em>{{ profileEntryStatus(profile) }}</em>
            </article>
            <div v-if="profiles.length === 1" class="library-empty-hint">
              <b>还没有本地 Profile</b>
              <span>复制 Factory 或点击“新建本地 Profile”，保存后会显示在这里。</span>
            </div>
          </div>
        </div>

        <div class="slot-pane">
          <header>
            <div><span class="tiny-label">DEVICE PROFILE PLAN · OFFLINE</span><h2>AK Ergo 77 · Slot 0–3</h2></div>
            <span class="chip amber"><i></i>设备未连接</span>
          </header>
          <div class="slot-track">
            <article
              v-for="slot in slots"
              :key="slot.index"
              class="slot"
              :class="{ active: slot.index === selectedSlotIndex, empty: !slot.profileId }"
              role="button"
              tabindex="0"
              style="cursor: pointer"
              @click="selectSlot(slot)"
              @keydown.enter="selectSlot(slot)"
              @keydown.space.prevent="selectSlot(slot)"
            >
              <span class="slot-index">{{ String(slot.index).padStart(2, '0') }}</span>
              <i v-if="profileForSlot(slot)" class="slot-swatch" :style="{ background: profileForSlot(slot)?.color }"></i>
              <i v-else></i>
              <div v-if="profileForSlot(slot)">
                <b>{{ profileForSlot(slot)?.name }}</b>
                <small v-if="slot.readonly">Factory recovery · 永久只读</small>
                <small v-else>本地分配草稿 · {{ slot.pending ? '有未提交变更' : '尚未读取设备' }}</small>
              </div>
              <div v-else><b>空 Slot</b><small>选择本地 Profile 后分配</small></div>
              <em class="chip" :class="slot.readonly ? 'blue' : slot.pending ? 'coral' : ''">{{ slot.readonly ? '只读' : slot.pending ? '本地变更' : '未核对' }}</em>
              <button v-if="!slot.readonly" aria-label="清空本地 Slot 计划" @click.stop="selectSlot(slot); clearSelectedSlot()">×</button>
              <button v-else disabled aria-label="Factory Slot 只读">⌁</button>
            </article>
          </div>
          <footer><span>Slot 0 是写保护 Factory；设备内容尚未读取</span><b>{{ usedSlotCount }} / 3 用户 Slot 已规划</b></footer>
        </div>
      </section>

      <div class="status-strip">
        <div class="status-item"><i class="status-icon">L</i><p><b>当前工作区 · {{ activeEntry.name }}</b><small>{{ activeWorkspaceStatus }}</small></p></div>
        <div class="status-item"><i class="status-icon">{{ selectedSlot.index }}</i><p><b>{{ selectedSlot.readonly ? 'Slot 0 · Factory 只读' : `目标 Slot ${selectedSlot.index}` }}</b><small>{{ localMessage }}</small></p></div>
        <button class="status-link" :disabled="!canAssignSelectedProfile" @click="assignSelectedProfile">{{ assignActionLabel }}</button>
      </div>
      <p v-if="library.persistenceError" class="profile-import-error" role="alert">{{ library.persistenceError }}</p>
      <p v-if="importError" class="profile-import-error" role="alert">{{ importError }}</p>
    </section>

    <aside class="page-inspector">
      <div class="inspector-inner">
        <header class="inspector-head">
          <p class="tiny-label">{{ selectedProfile.source === 'factory' ? '只读 Factory 来源' : '电脑中的本地 Profile' }}</p>
          <h2>{{ selectedProfile.name }} <small>r{{ selectedProfile.revision }}</small></h2>
          <p>{{ selectedIsActive ? selectedProfile.source === 'factory' && profileStore.isDirty ? '当前是恢复草稿，尚未成为新 Profile' : '当前正在编辑' : '已选择，尚未打开' }} · {{ selectedProfile.id }}</p>
        </header>
        <nav class="inspector-tabs" role="tablist" aria-label="Profile Slot 检查器">
          <button role="tab" :aria-selected="inspectorTab === 'details'" :class="{ active: inspectorTab === 'details' }" @click="inspectorTab = 'details'">详情</button>
          <button role="tab" :aria-selected="inspectorTab === 'diff'" :class="{ active: inspectorTab === 'diff' }" @click="inspectorTab = 'diff'">差异</button>
          <button role="tab" :aria-selected="inspectorTab === 'policy'" :class="{ active: inspectorTab === 'policy' }" @click="inspectorTab = 'policy'">策略</button>
        </nav>

        <div class="inspector-scroll">
          <template v-if="inspectorTab === 'details'">
            <section class="inspector-section">
              <div class="section-title"><h3>Profile 信息</h3><span class="chip" :class="selectedProfile.source === 'factory' ? 'blue' : 'amber'">{{ selectedProfile.source === 'factory' ? 'Factory' : '本地保存' }}</span></div>
              <div class="profile-meta-grid">
                <span>名称</span><b>{{ selectedProfile.name }}</b>
                <span>Profile ID</span><b class="mono">{{ selectedProfile.id }}</b>
                <span>修订</span><b>r{{ selectedProfile.revision }}</b>
                <span>当前槽位</span><b>{{ slots.filter((slot) => slot.profileId === selectedProfile.id).map((slot) => slot.index).join(', ') || '未分配' }}</b>
              </div>
            </section>
            <section class="inspector-section">
              <div class="notice" :class="{ warning: selectedIsActive && selectedProfile.source === 'factory' && profileStore.isDirty }">
                {{
                  selectedIsActive && selectedProfile.source === 'factory' && profileStore.isDirty
                    ? '当前修改只在恢复草稿里；点击“另存为本地副本”后，才会创建并切换到新的 Profile。'
                    : selectedProfile.source === 'factory'
                      ? 'Factory 来源始终只读。先复制为本地 Profile，再进行长期编辑和用户 Slot 分配。'
                      : '这份 Profile 已保存在电脑；工作区中的未保存修改还会额外写入恢复草稿。'
                }}
              </div>
            </section>
          </template>

          <template v-else-if="inspectorTab === 'diff'">
            <section class="inspector-section">
              <div class="section-title"><h3>同步差异</h3><span>等待设备数据</span></div>
              <div class="diff-list">
                <p><i class="diff-dot blue"></i><span><b>本地选择</b><small>{{ selectedProfile.name }} · r{{ selectedProfile.revision }}</small></span></p>
                <p><i class="diff-dot blue"></i><span><b>当前编辑草稿</b><small>{{ selectedIsActive ? profileStore.isDirty ? `${profileStore.dirtySections.length} 类未保存修改` : '与本地来源一致' : '打开后才能比较' }}</small></span></p>
                <p><i class="diff-dot coral"></i><span><b>设备 Slot {{ selectedSlot.index }}</b><small>尚未连接，无法计算真实差异</small></span></p>
              </div>
            </section>
            <section class="inspector-section"><div class="notice warning">连接并读取设备后，才会显示修订差异与可写入状态。</div></section>
          </template>

          <template v-else>
            <section class="inspector-section">
              <div class="section-title"><h3>Profile 内策略</h3><span>JSON source</span></div>
              <div class="profile-meta-grid">
                <span>数据版本</span><b>{{ selectedIsActive && profileStore.isDirty ? '当前工作草稿' : '本地已保存版本' }}</b>
                <span>USB 回报率</span><b>{{ selectedDocument.report_rate_policy.usb_report_rate_hz.toLocaleString() }} Hz</b>
                <span>CH585 回报率</span><b>{{ selectedDocument.report_rate_policy.ch585_wireless_report_rate_hz.toLocaleString() }} Hz</b>
                <span>Win 键锁定</span><b>{{ selectedDocument.input_guard_policy.win_key_lock_enabled ? '启用' : '关闭' }}</b>
                <span>宏定义</span><b>{{ macroCount }} 个</b>
              </div>
            </section>
            <section class="inspector-section"><div class="notice">这些值会保存进 Profile JSON。宏执行、回报率和输入保护的固件支持仍待实现。</div></section>
          </template>
        </div>

        <div class="inspector-actions">
          <button class="button quiet" @click="selectedProfile.source === 'factory' ? cloneFactory() : duplicateSelectedProfile()">{{ selectedProfile.source === 'factory' ? '复制 Factory' : '复制到本地库' }}</button>
          <button class="button primary" :disabled="selectedIsActive && selectedProfile.source !== 'factory' && !profileStore.isDirty" @click="saveOrOpenSelected">{{ primaryActionLabel }}</button>
        </div>
      </div>
    </aside>
  </div>
</template>
