<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import type { CSSProperties } from "vue";
import KeyboardBoard from "../components/keyboard/KeyboardBoard.vue";
import { formatSavedTime, loadLocalValue, saveLocalValue } from "../services/localPersistence";

type EffectId = "static" | "breathe" | "wave" | "reactive" | "temperature";
type InspectorTab = "effect" | "color" | "region";
type Direction = "north-east" | "east" | "south-east" | "south-west";
type ParameterKey =
  | "brightness"
  | "speed"
  | "spread"
  | "floor"
  | "decay"
  | "cooling"
  | "contrast";

interface EffectParameter {
  key: ParameterKey;
  label: string;
  min: number;
  max: number;
  suffix: string;
}

interface EffectDefinition {
  id: EffectId;
  label: string;
  code: string;
  summary: string;
  thumb: string;
  colorStops: 1 | 2;
  parameters: EffectParameter[];
}

interface LightingDraft {
  effectId: EffectId;
  direction: Direction;
  colorStart: string;
  colorEnd: string;
  undersideGlow: boolean;
  sleepOff: boolean;
  settings: Record<ParameterKey, number>;
}

const STORAGE_KEY = "kiiie.lighting-draft";
const STORAGE_VERSION = 1;

const effects: EffectDefinition[] = [
  {
    id: "static",
    label: "静态",
    code: "static_color",
    summary: "单色",
    thumb: "static",
    colorStops: 1,
    parameters: [{ key: "brightness", label: "亮度", min: 0, max: 100, suffix: "%" }],
  },
  {
    id: "breathe",
    label: "呼吸",
    code: "breathe",
    summary: "柔和明暗循环",
    thumb: "breathe",
    colorStops: 1,
    parameters: [
      { key: "brightness", label: "峰值亮度", min: 0, max: 100, suffix: "%" },
      { key: "speed", label: "呼吸速度", min: 1, max: 100, suffix: "%" },
      { key: "floor", label: "最低亮度", min: 0, max: 80, suffix: "%" },
    ],
  },
  {
    id: "wave",
    label: "棱镜波浪",
    code: "prism_wave",
    summary: "当前",
    thumb: "wave",
    colorStops: 2,
    parameters: [
      { key: "brightness", label: "亮度", min: 0, max: 100, suffix: "%" },
      { key: "speed", label: "速度", min: 1, max: 100, suffix: "%" },
      { key: "spread", label: "扩散宽度", min: 1, max: 100, suffix: "%" },
    ],
  },
  {
    id: "reactive",
    label: "触发涟漪",
    code: "reactive_ripple",
    summary: "按键响应",
    thumb: "reactive",
    colorStops: 2,
    parameters: [
      { key: "brightness", label: "亮度", min: 0, max: 100, suffix: "%" },
      { key: "decay", label: "衰减时间", min: 1, max: 100, suffix: "%" },
      { key: "spread", label: "涟漪范围", min: 1, max: 100, suffix: "%" },
    ],
  },
  {
    id: "temperature",
    label: "行程热度",
    code: "travel_heat",
    summary: "磁轴数据",
    thumb: "temperature",
    colorStops: 2,
    parameters: [
      { key: "brightness", label: "亮度", min: 0, max: 100, suffix: "%" },
      { key: "cooling", label: "冷却速度", min: 1, max: 100, suffix: "%" },
      { key: "contrast", label: "热区对比", min: 1, max: 100, suffix: "%" },
    ],
  },
];

const restoredDraft = loadLocalValue<LightingDraft>(STORAGE_KEY, STORAGE_VERSION);
const restored = restoredDraft?.data;
const inspectorTab = ref<InspectorTab>("effect");
const effectId = ref<EffectId>(restored?.effectId ?? "wave");
const direction = ref<Direction>(restored?.direction ?? "east");
const selectedIds = ref<string[]>([]);
const colorStart = ref(restored?.colorStart ?? "#526bff");
const colorEnd = ref(restored?.colorEnd ?? "#ff718d");
const undersideGlow = ref(restored?.undersideGlow ?? true);
const sleepOff = ref(restored?.sleepOff ?? true);
const savedAt = ref(formatSavedTime(restoredDraft?.savedAt ?? null));
const savedSignature = ref("");
const settings = reactive<Record<ParameterKey, number>>({
  brightness: restored?.settings.brightness ?? 68,
  speed: restored?.settings.speed ?? 42,
  spread: restored?.settings.spread ?? 56,
  floor: restored?.settings.floor ?? 12,
  decay: restored?.settings.decay ?? 38,
  cooling: restored?.settings.cooling ?? 48,
  contrast: restored?.settings.contrast ?? 64,
});

const activeEffect = computed(() => effects.find((effect) => effect.id === effectId.value) ?? effects[2]);
const draftSignature = computed(() =>
  JSON.stringify({
    effectId: effectId.value,
    direction: direction.value,
    colorStart: colorStart.value,
    colorEnd: colorEnd.value,
    undersideGlow: undersideGlow.value,
    sleepOff: sleepOff.value,
    settings,
  }),
);
const isDirty = computed(() => savedSignature.value !== draftSignature.value);
const stageStyle = computed<CSSProperties>(() => ({
  "--light-color": colorStart.value,
  "--lighting-color": colorStart.value,
  "--lighting-end": colorEnd.value,
  "--lighting-intensity": (settings.brightness / 100).toFixed(2),
  "--breathe-floor": (settings.floor / 100).toFixed(2),
  "--breathe-duration": `${Math.max(1.4, 6.4 - settings.speed * 0.05).toFixed(2)}s`,
} as CSSProperties));
const colorRampStyle = computed<CSSProperties>(() => ({
  background:
    activeEffect.value.colorStops === 1
      ? colorStart.value
      : `linear-gradient(90deg, ${colorStart.value}, #9b65e9 48%, ${colorEnd.value})`,
}));

savedSignature.value = draftSignature.value;

function chooseEffect(id: EffectId): void {
  effectId.value = id;
}

function selectControl(payload: { id: string }): void {
  selectedIds.value = [payload.id];
}

function clearSelection(): void {
  selectedIds.value = [];
}

function restorePreset(): void {
  effectId.value = "wave";
  direction.value = "east";
  colorStart.value = "#526bff";
  colorEnd.value = "#ff718d";
  undersideGlow.value = true;
  sleepOff.value = true;
  Object.assign(settings, {
    brightness: 68,
    speed: 42,
    spread: 56,
    floor: 12,
    decay: 38,
    cooling: 48,
    contrast: 64,
  });
}

function saveLocalDraft(): void {
  const saved = saveLocalValue<LightingDraft>(STORAGE_KEY, STORAGE_VERSION, {
    effectId: effectId.value,
    direction: direction.value,
    colorStart: colorStart.value,
    colorEnd: colorEnd.value,
    undersideGlow: undersideGlow.value,
    sleepOff: sleepOff.value,
    settings: { ...settings },
  });
  savedSignature.value = draftSignature.value;
  savedAt.value = formatSavedTime(saved);
}
</script>

<template>
  <div class="page-shell">
    <section class="page-main lighting-main">
      <header class="page-heading">
        <div>
          <p class="page-kicker">LIGHTING CONFIG / LOCAL DRAFT</p>
          <h1>灯效工作室</h1>
          <p class="lede">灯光只存在于键帽缝隙与机身边缘；调整颜色与节奏，不牺牲键盘本体的可读性。</p>
        </div>
        <div class="heading-actions">
          <span class="chip amber"><i></i>协议待接入</span>
          <button class="button primary" type="button" @click="saveLocalDraft">保存本地草稿</button>
        </div>
      </header>

      <section
        class="lighting-stage keyboard-surface light-studio"
        :data-effect="effectId"
        :data-glow="undersideGlow ? 'on' : 'off'"
        :style="stageStyle"
      >
        <div class="ambient-orbit orbit-a" aria-hidden="true"></div>
        <div class="ambient-orbit orbit-b" aria-hidden="true"></div>
        <KeyboardBoard class="lighting-board" :selected-ids="selectedIds" @select="selectControl" @clear="clearSelection" />

        <div class="effect-filmstrip" aria-label="灯效选择">
          <button
            v-for="effect in effects"
            :key="effect.id"
            type="button"
            :class="{ active: effect.id === effectId }"
            :aria-pressed="effect.id === effectId"
            @click="chooseEffect(effect.id)"
          >
            <i class="effect-thumb" :class="effect.thumb" aria-hidden="true"></i>
            <span><b>{{ effect.label }}</b><small>{{ effect.id === effectId ? "当前" : effect.summary }}</small></span>
          </button>
        </div>
      </section>
    </section>

    <aside class="page-inspector">
      <div class="inspector-inner">
        <header class="inspector-head">
          <p class="tiny-label">灯效预设 · 本地草稿</p>
          <h2>{{ activeEffect.label }} <small>01</small></h2>
          <p>全键区 · {{ activeEffect.code }} · {{ settings.brightness }}% 亮度</p>
        </header>

        <nav class="inspector-tabs" role="tablist" aria-label="灯效设置类型">
          <button role="tab" :aria-selected="inspectorTab === 'effect'" :class="{ active: inspectorTab === 'effect' }" @click="inspectorTab = 'effect'">效果</button>
          <button role="tab" :aria-selected="inspectorTab === 'color'" :class="{ active: inspectorTab === 'color' }" @click="inspectorTab = 'color'">颜色</button>
          <button role="tab" :aria-selected="inspectorTab === 'region'" :class="{ active: inspectorTab === 'region' }" @click="inspectorTab = 'region'">区域</button>
        </nav>

        <div class="inspector-scroll">
          <template v-if="inspectorTab === 'effect'">
            <section class="inspector-section">
              <div class="section-title"><h3>效果</h3><span class="chip blue">全键区</span></div>
              <div class="select-field binding-select-field">
                <i class="field-icon gradient-icon" aria-hidden="true"></i>
                <span><strong>{{ activeEffect.label }}</strong><small>{{ activeEffect.code }} · {{ activeEffect.summary }}</small></span>
                <select v-model="effectId" aria-label="选择灯效">
                  <option v-for="effect in effects" :key="effect.id" :value="effect.id">{{ effect.label }}</option>
                </select>
                <i class="field-chevron">⌄</i>
              </div>
            </section>

            <section class="inspector-section">
              <div class="section-title"><h3>效果参数</h3><span>随效果变化</span></div>
              <label v-for="parameter in activeEffect.parameters" :key="parameter.key" class="native-parameter">
                <span>{{ parameter.label }}</span>
                <b>{{ settings[parameter.key] }}{{ parameter.suffix }}</b>
                <input
                  v-model.number="settings[parameter.key]"
                  type="range"
                  :min="parameter.min"
                  :max="parameter.max"
                  :aria-label="parameter.label"
                />
              </label>
            </section>

            <section class="inspector-section">
              <div class="notice">当前参数只驱动本地预览；灯效资源的设备协议与持久化格式尚未接入。</div>
            </section>
          </template>

          <template v-else-if="inspectorTab === 'color'">
            <section class="inspector-section">
              <div class="section-title"><h3>颜色范围</h3><span>sRGB</span></div>
              <div class="color-ramp" :style="colorRampStyle">
                <span class="color-handle first" :style="{ background: colorStart }"></span>
                <span
                  v-if="activeEffect.colorStops === 2"
                  class="color-handle second"
                  :style="{ background: colorEnd }"
                ></span>
              </div>
              <div class="color-values">
                <label><input v-model="colorStart" type="color" aria-label="起始颜色" /><span>{{ colorStart.toUpperCase() }}</span></label>
                <label v-if="activeEffect.colorStops === 2"><input v-model="colorEnd" type="color" aria-label="结束颜色" /><span>{{ colorEnd.toUpperCase() }}</span></label>
              </div>
            </section>

            <section class="inspector-section">
              <div class="section-title"><h3>颜色用途</h3><span>{{ activeEffect.colorStops }} 个色标</span></div>
              <div class="notice" v-if="activeEffect.colorStops === 1">{{ activeEffect.label }}使用单一主色；亮度由“效果”页单独控制。</div>
              <div class="notice" v-else>两端色标用于本地渐变预览；中间过渡色由界面计算，不代表最终设备算法。</div>
            </section>
          </template>

          <template v-else>
            <section class="inspector-section">
              <div class="section-title"><h3>方向与作用区</h3><span>本地预览</span></div>
              <div class="direction-picker">
                <button :class="{ active: direction === 'north-east' }" @click="direction = 'north-east'">↗</button>
                <button :class="{ active: direction === 'east' }" @click="direction = 'east'">→</button>
                <button :class="{ active: direction === 'south-east' }" @click="direction = 'south-east'">↘</button>
                <button :class="{ active: direction === 'south-west' }" @click="direction = 'south-west'">↙</button>
              </div>
              <div class="toggle-line">
                <span><b>键帽底部发光</b><small>保持字符表面为白色</small></span>
                <button class="switch" :class="{ on: undersideGlow }" role="switch" :aria-checked="undersideGlow" @click="undersideGlow = !undersideGlow"></button>
              </div>
              <div class="toggle-line">
                <span><b>设备休眠时关闭</b><small>仅记录意图，等待电源策略接入</small></span>
                <button class="switch" :class="{ on: sleepOff }" role="switch" :aria-checked="sleepOff" @click="sleepOff = !sleepOff"></button>
              </div>
            </section>
            <section class="inspector-section"><div class="notice warning">当前仅支持全键区预览；分区和逐键范围会在资源协议确定后建模。</div></section>
          </template>
        </div>

        <div class="inspector-actions">
          <button class="button quiet" type="button" @click="restorePreset">恢复预设</button>
          <button class="button primary" type="button" :disabled="!isDirty" @click="saveLocalDraft">
            {{ isDirty ? "保存本地草稿" : savedAt === "尚未保存" ? "草稿无更改" : `已保存 ${savedAt}` }}
          </button>
        </div>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.lighting-board :deep(.keyboard-map) { top: 46px; }
.lighting-stage[data-glow="on"] .lighting-board :deep(.keycap-light) {
  opacity: calc(.22 + var(--lighting-intensity) * .68);
}
.lighting-stage[data-glow="off"] .lighting-board :deep(.keycap-light) { opacity: 0; }
.lighting-board :deep(.keycap-light) {
  --key-light-color: var(--lighting-color);
  background: var(--key-light-color);
  box-shadow: 0 4px 13px var(--key-light-color), 0 8px 25px color-mix(in srgb, var(--key-light-color) 55%, transparent);
}
.lighting-stage[data-effect="wave"] .lighting-board :deep(.keycap:nth-of-type(5n + 1) .keycap-light) { --key-light-color: #6d79ff; }
.lighting-stage[data-effect="wave"] .lighting-board :deep(.keycap:nth-of-type(5n + 2) .keycap-light) { --key-light-color: #8b62ff; }
.lighting-stage[data-effect="wave"] .lighting-board :deep(.keycap:nth-of-type(5n + 3) .keycap-light) { --key-light-color: #e468ca; }
.lighting-stage[data-effect="wave"] .lighting-board :deep(.keycap:nth-of-type(5n + 4) .keycap-light) { --key-light-color: var(--lighting-end); }
.lighting-stage[data-effect="wave"] .lighting-board :deep(.keycap:nth-of-type(5n) .keycap-light) { --key-light-color: #65b7ff; }
.lighting-stage[data-effect="temperature"] .lighting-board :deep(.keycap:nth-of-type(4n + 1) .keycap-light) { --key-light-color: #5c76ff; }
.lighting-stage[data-effect="temperature"] .lighting-board :deep(.keycap:nth-of-type(4n + 2) .keycap-light) { --key-light-color: #56c9c0; }
.lighting-stage[data-effect="temperature"] .lighting-board :deep(.keycap:nth-of-type(4n + 3) .keycap-light) { --key-light-color: #ffd36c; }
.lighting-stage[data-effect="temperature"] .lighting-board :deep(.keycap:nth-of-type(4n) .keycap-light) { --key-light-color: #ff715b; }
.lighting-stage[data-effect="reactive"] .lighting-board :deep(.keycap-light) { opacity: .16; }
.lighting-stage[data-effect="reactive"] .lighting-board :deep(.keycap.selected .keycap-light) { opacity: .95; --key-light-color: var(--lighting-end); }
.lighting-stage[data-effect="breathe"] .lighting-board :deep(.keycap-light) {
  animation: local-breathe var(--breathe-duration) ease-in-out infinite alternate;
}
.color-values label { display: flex; align-items: center; gap: 6px; }
.color-values input { width: 12px; height: 12px; padding: 0; overflow: hidden; border: 0; border-radius: 50%; background: transparent; }
.color-values input::-webkit-color-swatch-wrapper { padding: 0; }
.color-values input::-webkit-color-swatch { border: 0; border-radius: 50%; }
.switch { padding: 0; }

@keyframes local-breathe {
  from { opacity: var(--breathe-floor); }
  to { opacity: calc(.22 + var(--lighting-intensity) * .68); }
}

@media (prefers-reduced-motion: reduce) {
  .lighting-stage[data-effect="breathe"] .lighting-board :deep(.keycap-light) { animation: none; }
}
</style>
