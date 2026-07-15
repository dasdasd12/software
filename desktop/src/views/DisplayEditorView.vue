<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref } from "vue";
import type { CSSProperties } from "vue";
import { formatSavedTime, loadLocalValue, saveLocalValue } from "../services/localPersistence";

type InspectorTab = "layout" | "media" | "page";
type PageId = "status" | "input" | "profile" | "media";
type FitMode = "contain" | "cover";

interface LocalAsset {
  name: string;
  kind: "图片" | "动图";
  mime: string;
  size: number;
  width: number | null;
  height: number | null;
  url: string;
}

interface WidgetState {
  id: string;
  label: string;
  detail: string;
  enabled: boolean;
}

interface DisplayDraft {
  activePage: Exclude<PageId, "media">;
  selectedWidgetId: string;
  fitMode: FitMode;
  zoom: number;
  cornerRadius: number;
  lockRatio: boolean;
  geometry: { x: number; y: number; width: number; height: number };
  widgets: Array<{ id: string; enabled: boolean }>;
}

const STORAGE_KEY = "kiiie.display-layout-draft";
const STORAGE_VERSION = 1;
const restoredDraft = loadLocalValue<DisplayDraft>(STORAGE_KEY, STORAGE_VERSION);
const restored = restoredDraft?.data;
const defaultWidgets: WidgetState[] = [
  { id: "profile", label: "当前 Profile", detail: "Factory Default", enabled: true },
  { id: "travel", label: "行程状态", detail: "选中键 · 40%", enabled: true },
  { id: "connection", label: "连接与电量", detail: "USB · 82%", enabled: true },
  { id: "agent", label: "Agent 状态", detail: "PC 连接时显示", enabled: false },
];

const fileInput = ref<HTMLInputElement | null>(null);
const inspectorTab = ref<InspectorTab>("layout");
const activePage = ref<PageId>(restored?.activePage ?? "input");
const selectedWidgetId = ref(restored?.selectedWidgetId ?? "travel");
const localAsset = ref<LocalAsset | null>(null);
const uploadError = ref("");
const fitMode = ref<FitMode>(restored?.fitMode ?? "contain");
const zoom = ref(restored?.zoom ?? 75);
const cornerRadius = ref(restored?.cornerRadius ?? 18);
const lockRatio = ref(restored?.lockRatio ?? true);
const savedAt = ref(formatSavedTime(restoredDraft?.savedAt ?? null));
const savedSignature = ref("");
const geometry = reactive({ x: 52, y: 146, width: 106, height: 106, ...restored?.geometry });
const widgets = reactive<WidgetState[]>(defaultWidgets.map((widget) => ({
  ...widget,
  enabled: restored?.widgets.find((candidate) => candidate.id === widget.id)?.enabled ?? widget.enabled,
})));

const activeWidget = computed(() => widgets.find((widget) => widget.id === selectedWidgetId.value) ?? widgets[1]);
const screenTitle = computed(() => {
  if (activePage.value === "status") return "设备状态";
  if (activePage.value === "profile") return "当前 Profile";
  return "输入已触发";
});
const screenEyebrow = computed(() => activePage.value === "profile" ? "FACTORY DEFAULT" : "Rapid Trigger");
const bezelStyle = computed<CSSProperties>(() => ({
  width: `min(${Math.round(690 * zoom.value / 75)}px, 94%)`,
}));
const selectionStyle = computed<CSSProperties>(() => ({
  width: `${geometry.width}px`,
  height: `${geometry.height}px`,
}));
const assetMeta = computed(() => {
  const asset = localAsset.value;
  if (!asset) return "尚未选择素材";
  const dimensions = asset.width && asset.height ? `${asset.width} × ${asset.height}` : "正在读取尺寸";
  return `${asset.kind} · ${dimensions} · ${formatFileSize(asset.size)}`;
});
const draftSignature = computed(() =>
  JSON.stringify({
    activePage: activePage.value,
    selectedWidgetId: selectedWidgetId.value,
    asset: localAsset.value
      ? { name: localAsset.value.name, size: localAsset.value.size, mime: localAsset.value.mime }
      : null,
    fitMode: fitMode.value,
    zoom: zoom.value,
    cornerRadius: cornerRadius.value,
    lockRatio: lockRatio.value,
    geometry,
    widgets: widgets.map(({ id, enabled }) => ({ id, enabled })),
  }),
);
const isDirty = computed(() => savedSignature.value !== draftSignature.value);

savedSignature.value = draftSignature.value;

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function openFilePicker(): void {
  fileInput.value?.click();
}

function choosePage(page: PageId): void {
  if (page === "media" && !localAsset.value) {
    openFilePicker();
    return;
  }
  activePage.value = page;
}

function releaseAssetUrl(): void {
  if (localAsset.value?.url) URL.revokeObjectURL(localAsset.value.url);
}

function loadAsset(event: Event): void {
  const input = event.currentTarget as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file) return;
  if (!file.type.startsWith("image/")) {
    uploadError.value = "只支持图片或 GIF 动图文件。";
    return;
  }

  uploadError.value = "";
  releaseAssetUrl();
  const url = URL.createObjectURL(file);
  const isAnimated = file.type === "image/gif" || file.name.toLowerCase().endsWith(".gif");
  localAsset.value = {
    name: file.name,
    kind: isAnimated ? "动图" : "图片",
    mime: file.type || "image/*",
    size: file.size,
    width: null,
    height: null,
    url,
  };
  activePage.value = "media";
  inspectorTab.value = "media";

  const probe = new Image();
  probe.onload = () => {
    if (localAsset.value?.url !== url) return;
    localAsset.value = { ...localAsset.value, width: probe.naturalWidth, height: probe.naturalHeight };
  };
  probe.src = url;
}

function removeAsset(): void {
  releaseAssetUrl();
  localAsset.value = null;
  activePage.value = "input";
}

function adjustZoom(delta: number): void {
  zoom.value = Math.min(125, Math.max(50, zoom.value + delta));
}

function resetPage(): void {
  activePage.value = "input";
  selectedWidgetId.value = "travel";
  fitMode.value = "contain";
  zoom.value = 75;
  cornerRadius.value = 18;
  lockRatio.value = true;
  Object.assign(geometry, { x: 52, y: 146, width: 106, height: 106 });
  widgets.forEach((widget) => {
    widget.enabled = widget.id !== "agent";
  });
}

function saveLocalDraft(): void {
  const saved = saveLocalValue<DisplayDraft>(STORAGE_KEY, STORAGE_VERSION, {
    activePage: activePage.value === "media" ? "input" : activePage.value,
    selectedWidgetId: selectedWidgetId.value,
    fitMode: fitMode.value,
    zoom: zoom.value,
    cornerRadius: cornerRadius.value,
    lockRatio: lockRatio.value,
    geometry: { ...geometry },
    widgets: widgets.map(({ id, enabled }) => ({ id, enabled })),
  });
  savedSignature.value = draftSignature.value;
  savedAt.value = formatSavedTime(saved);
}

onBeforeUnmount(releaseAssetUrl);
</script>

<template>
  <div class="page-shell">
    <section class="page-main display-main">
      <header class="page-heading">
        <div>
          <p class="page-kicker">SCREEN CONFIG / LOCAL PREVIEW</p>
          <h1>屏显编辑器</h1>
          <p class="lede">在 800 × 480 的 5:3 画布中预览设备页面，也可以选择图片或 GIF 动图作为本地素材。</p>
        </div>
        <div class="heading-actions">
          <span class="chip amber"><i></i>协议待接入</span>
          <button class="button secondary" type="button" @click="openFilePicker">选择图片 / 动图</button>
          <button class="button primary" type="button" @click="saveLocalDraft">保存本地草稿</button>
          <input
            ref="fileInput"
            class="visually-hidden-file"
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif,.gif"
            @change="loadAsset"
          />
        </div>
      </header>

      <section class="display-studio">
        <div class="screen-device-shell">
          <div class="screen-canvas-toolbar">
            <span><i></i>页面画布 <b>800 × 480 · 5:3</b></span>
            <div>
              <button type="button" aria-label="缩小画布" @click="adjustZoom(-5)">−</button>
              <b>{{ zoom }}%</b>
              <button type="button" aria-label="放大画布" @click="adjustZoom(5)">＋</button>
              <button class="fit" type="button" @click="zoom = 75">适应</button>
            </div>
          </div>

          <div class="screen-bezel" :style="bezelStyle">
            <div class="screen-preview">
              <template v-if="activePage === 'media' && localAsset">
                <img class="uploaded-media" :src="localAsset.url" :alt="localAsset.name" :style="{ objectFit: fitMode }" />
                <div class="media-overlay">
                  <span>{{ localAsset.kind }} · 本地预览</span>
                  <b>{{ localAsset.name }}</b>
                </div>
              </template>

              <template v-else>
                <header><span><i></i>FACTORY DEFAULT</span><em>USB · 82%</em></header>
                <div class="screen-content">
                  <div class="screen-selection" :style="selectionStyle">
                    <em>{{ activeWidget.label }} · {{ geometry.width }} × {{ geometry.height }}</em>
                    <i class="handle nw"></i><i class="handle ne"></i><i class="handle sw"></i><i class="handle se"></i>
                    <div class="screen-key" :style="{ width: `${geometry.width}px`, height: `${geometry.height}px`, borderRadius: `${cornerRadius}px` }">
                      <span>A</span><i style="height: 40%"></i><b>40%</b>
                    </div>
                  </div>
                  <div class="screen-copy">
                    <p>{{ screenEyebrow }}</p>
                    <h2>{{ screenTitle }}</h2>
                    <small>释放灵敏度 10% · 8,000 Hz</small>
                    <div class="screen-spark"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div>
                  </div>
                </div>
                <footer><span>Profile 01</span><span>CH585 LINK · GOOD</span><b>12:48</b></footer>
              </template>
            </div>
          </div>

          <div class="screen-canvas-meta">
            <span>安全区 24 px</span>
            <span>{{ localAsset ? `${localAsset.kind} · 当前会话` : "RGB565 · 键盘本地渲染" }}</span>
          </div>
        </div>

        <div class="page-filmstrip" aria-label="屏显页面选择">
          <button type="button" :class="{ active: activePage === 'status' }" @click="choosePage('status')"><i class="page-thumb status"></i><span>设备状态</span></button>
          <button type="button" :class="{ active: activePage === 'input' }" @click="choosePage('input')"><i class="page-thumb input"></i><span>输入状态</span></button>
          <button type="button" :class="{ active: activePage === 'profile' }" @click="choosePage('profile')"><i class="page-thumb profile"></i><span>Profile</span></button>
          <button type="button" :class="{ active: activePage === 'media' }" @click="choosePage('media')">
            <i class="page-thumb media" :class="{ animated: localAsset?.kind === '动图' }"></i>
            <span>{{ localAsset ? localAsset.kind : "本地素材" }}</span>
          </button>
          <button type="button" @click="openFilePicker"><i class="page-thumb blank"></i><span>＋ 上传素材</span></button>
        </div>
      </section>
    </section>

    <aside class="page-inspector">
      <div class="inspector-inner">
        <header class="inspector-head">
          <p class="tiny-label">当前选中 · 本地草稿</p>
          <h2>{{ activePage === "media" && localAsset ? localAsset.name : activeWidget.label }} <small>{{ activePage === "media" ? "MEDIA" : "WIDGET" }}</small></h2>
          <p>{{ activePage === "media" ? assetMeta : `widget.${activeWidget.id} · ${geometry.width} × ${geometry.height}` }}</p>
        </header>

        <nav class="inspector-tabs" role="tablist" aria-label="屏显编辑类型">
          <button role="tab" :aria-selected="inspectorTab === 'layout'" :class="{ active: inspectorTab === 'layout' }" @click="inspectorTab = 'layout'">布局</button>
          <button role="tab" :aria-selected="inspectorTab === 'media'" :class="{ active: inspectorTab === 'media' }" @click="inspectorTab = 'media'">素材</button>
          <button role="tab" :aria-selected="inspectorTab === 'page'" :class="{ active: inspectorTab === 'page' }" @click="inspectorTab = 'page'">页面</button>
        </nav>

        <div class="inspector-scroll">
          <template v-if="inspectorTab === 'layout'">
            <section class="inspector-section">
              <div class="section-title"><h3>选中组件</h3><span>WIDGET 02</span></div>
              <div class="select-field">
                <i class="field-icon display-icon">A</i>
                <span><strong>{{ activeWidget.label }}</strong><small>widget.{{ activeWidget.id }}</small></span>
                <i class="field-chevron">⌄</i>
              </div>
            </section>

            <section class="inspector-section">
              <div class="section-title"><h3>位置与尺寸</h3><span>px · 本地预览</span></div>
              <div class="property-grid editable-properties">
                <label><span>X</span><input v-model.number="geometry.x" type="number" min="0" max="800" /></label>
                <label><span>Y</span><input v-model.number="geometry.y" type="number" min="0" max="480" /></label>
                <label><span>W</span><input v-model.number="geometry.width" type="number" min="40" max="300" /></label>
                <label><span>H</span><input v-model.number="geometry.height" type="number" min="40" max="240" /></label>
              </div>
              <div class="alignment-row">
                <span>锚点</span>
                <div class="anchor-picker"><i></i><i></i><i></i><i></i><i class="active"></i><i></i><i></i><i></i><i></i></div>
                <button type="button" @click="geometry.x = 347; geometry.y = 187">居中对齐</button>
              </div>
            </section>

            <section class="inspector-section">
              <label class="native-parameter"><span>组件圆角</span><b>{{ cornerRadius }} px</b><input v-model.number="cornerRadius" type="range" min="0" max="40" /></label>
              <div class="toggle-line">
                <span><b>锁定宽高比</b><small>调整尺寸时保持 1:1</small></span>
                <button class="switch" :class="{ on: lockRatio }" role="switch" :aria-checked="lockRatio" @click="lockRatio = !lockRatio"></button>
              </div>
            </section>
          </template>

          <template v-else-if="inspectorTab === 'media'">
            <section class="inspector-section">
              <div class="section-title"><h3>图片与动图</h3><span>当前会话</span></div>
              <button class="media-drop" type="button" @click="openFilePicker">
                <i>＋</i><span><b>{{ localAsset ? "更换素材" : "选择本地文件" }}</b><small>PNG / JPG / WebP / GIF</small></span>
              </button>
              <p v-if="uploadError" class="upload-error" role="alert">{{ uploadError }}</p>
            </section>

            <section v-if="localAsset" class="inspector-section">
              <div class="section-title"><h3>当前素材</h3><span class="chip blue">{{ localAsset.kind }}</span></div>
              <div class="asset-summary"><b>{{ localAsset.name }}</b><small>{{ assetMeta }}</small></div>
              <label class="field-label fit-label">画布适配</label>
              <div class="segmented fit-picker">
                <button :class="{ active: fitMode === 'contain' }" @click="fitMode = 'contain'">完整显示</button>
                <button :class="{ active: fitMode === 'cover' }" @click="fitMode = 'cover'">铺满裁切</button>
              </div>
              <button class="inline-link remove-media" type="button" @click="removeAsset">移除当前素材 <span>返回输入状态 →</span></button>
            </section>

            <section class="inspector-section"><div class="notice">文件仅通过浏览器对象 URL 在本次会话预览，不会上传、写入设备或自动加入 Profile。</div></section>
          </template>

          <template v-else>
            <section class="inspector-section compact-section">
              <div class="section-title"><h3>页面组件</h3><span>{{ widgets.filter((widget) => widget.enabled).length }} 个启用</span></div>
              <div class="widget-list">
                <div
                  v-for="widget in widgets"
                  :key="widget.id"
                  :class="{ selected: selectedWidgetId === widget.id }"
                  @click="selectedWidgetId = widget.id"
                >
                  <i class="drag-handle">⠿</i>
                  <span><b>{{ widget.label }}</b><small>{{ widget.detail }}</small></span>
                  <button class="switch" :class="{ on: widget.enabled }" role="switch" :aria-checked="widget.enabled" @click.stop="widget.enabled = !widget.enabled"></button>
                </div>
              </div>
            </section>
            <section class="inspector-section"><div class="notice warning">组件顺序和开关目前只更新本地草稿；屏显资源协议确定后再映射到设备数据。</div></section>
          </template>
        </div>

        <div class="inspector-actions">
          <button class="button quiet" type="button" @click="resetPage">恢复页面</button>
          <button class="button primary" type="button" :disabled="!isDirty" @click="saveLocalDraft">
            {{ isDirty ? "保存本地草稿" : savedAt === "尚未保存" ? "草稿无更改" : `已保存 ${savedAt}` }}
          </button>
        </div>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.visually-hidden-file {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
.screen-bezel { transition: width 160ms ease; }
.screen-preview { position: relative; }
.uploaded-media { position: absolute; inset: 0; width: 100%; height: 100%; background: #0b111b; }
.media-overlay {
  position: absolute;
  left: 16px;
  right: 16px;
  bottom: 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 9px 12px;
  border: 1px solid rgba(255,255,255,.12);
  border-radius: 8px;
  color: #dce4ff;
  background: rgba(11,17,27,.68);
  backdrop-filter: blur(9px);
  font-size: 8px;
}
.media-overlay span { color: #9eaac0; }
.media-overlay b { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: 8px/1 var(--font-utility); }
.page-thumb.media { background-image: linear-gradient(135deg, #526bff, #a663e5 48%, #ff718d); }
.page-thumb.media.animated { background-image: repeating-linear-gradient(135deg, #526bff 0 8px, #a663e5 8px 16px, #ff718d 16px 24px); }
.editable-properties input {
  min-width: 0;
  width: 100%;
  height: 100%;
  padding: 0 8px;
  border: 0;
  outline: 0;
  color: var(--ink-soft);
  background: white;
  font: 8px/1 var(--font-utility);
}
.switch { padding: 0; }
.widget-list > div { cursor: pointer; }
.media-drop {
  width: 100%;
  min-height: 74px;
  display: grid;
  grid-template-columns: 31px 1fr;
  align-items: center;
  gap: 11px;
  padding: 0 13px;
  text-align: left;
  border: 1px dashed var(--line-strong);
  border-radius: 10px;
  color: var(--ink-soft);
  background: var(--canvas);
}
.media-drop > i { width: 29px; height: 29px; display: grid; place-items: center; border-radius: 8px; color: var(--accent-strong); background: white; font-style: normal; }
.media-drop > span { display: grid; gap: 4px; }
.media-drop b { font-size: 9px; }
.media-drop small { color: var(--muted); font-size: 8px; }
.upload-error { margin-top: 9px; color: #b84c3b; font-size: 8px; }
.asset-summary { display: grid; gap: 5px; min-width: 0; }
.asset-summary b { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 9px; }
.asset-summary small { color: var(--muted); font: 7px/1.4 var(--font-utility); }
.fit-label { margin-top: 15px; }
.fit-picker { width: 100%; }
.fit-picker button { flex: 1; }
.remove-media { margin-top: 15px; }

@media (prefers-reduced-motion: reduce) {
  .screen-bezel { transition: none; }
}
</style>
