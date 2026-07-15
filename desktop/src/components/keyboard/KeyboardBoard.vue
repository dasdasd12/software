<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { CSSProperties } from "vue";
import {
  KEYBOARD_BOUNDS,
  KEYBOARD_HARDWARE,
  KEYBOARD_KEYS,
  type KeyboardKey,
  type Rect,
} from "../../domain/keyboard/layout";

const props = defineProps<{
  selectedIds: string[];
  dirtyIds?: string[];
}>();

const emit = defineEmits<{
  select: [payload: { id: string; additive: boolean; range: boolean }];
  clear: [];
}>();

const stage = ref<HTMLElement | null>(null);
const scale = ref(0.57);
let resizeObserver: ResizeObserver | null = null;

function physicalControlId(controlId: string): string {
  if (controlId.startsWith("fiveway_000.")) return "fiveway_000";
  if (controlId.startsWith("enc_000.")) return "enc_000";
  return controlId;
}

const initialSelection = props.selectedIds[props.selectedIds.length - 1] ?? KEYBOARD_KEYS[0]?.id ?? "";
const focusId = ref(physicalControlId(initialSelection));
const focusTargets = [
  ...KEYBOARD_KEYS.map((key) => ({ id: key.id, x: key.x, y: key.y })),
  {
    id: "fiveway_000",
    x: KEYBOARD_HARDWARE.fiveway.x + KEYBOARD_HARDWARE.fiveway.w / 2,
    y: KEYBOARD_HARDWARE.fiveway.y + KEYBOARD_HARDWARE.fiveway.h / 2,
  },
  {
    id: "enc_000",
    x: KEYBOARD_HARDWARE.encoder.x + KEYBOARD_HARDWARE.encoder.w / 2,
    y: KEYBOARD_HARDWARE.encoder.y + KEYBOARD_HARDWARE.encoder.h / 2,
  },
];

const rootStyle = computed<CSSProperties>(() => ({
  "--keyboard-scale": scale.value.toFixed(4),
  "--keyboard-width": `${KEYBOARD_BOUNDS.width}px`,
  "--keyboard-height": `${KEYBOARD_BOUNDS.height}px`,
} as CSSProperties));

function rectStyle(rect: Rect): CSSProperties {
  return {
    left: `${rect.x}px`,
    top: `${rect.y}px`,
    width: `${rect.w}px`,
    height: `${rect.h}px`,
  };
}

function keyStyle(key: Readonly<KeyboardKey>): CSSProperties {
  const angle = key.row === 1 ? 0 : key.side === "left" ? 6 : -6;
  return {
    left: `${key.x - key.w / 2}px`,
    top: `${key.y - key.h / 2}px`,
    width: `${key.w}px`,
    height: `${key.h}px`,
    transform: `rotate(${angle}deg)`,
  };
}

function isSelected(id: string): boolean {
  return props.selectedIds.includes(id);
}

function isHardwareSelected(prefix: string): boolean {
  return props.selectedIds.some((id) => id.startsWith(`${prefix}.`));
}

function isHardwareDirty(prefix: string): boolean {
  return props.dirtyIds?.some((id) => id.startsWith(`${prefix}.`)) ?? false;
}

function select(id: string, event: MouseEvent): void {
  emit("select", {
    id,
    additive: event.ctrlKey || event.metaKey || event.shiftKey,
    range: event.shiftKey,
  });
}

function clearFromSurface(event: MouseEvent): void {
  const target = event.target;
  if (!(target instanceof Element) || target.closest("button")) return;
  emit("clear");
}

function navigateFocus(currentId: string, event: KeyboardEvent): void {
  const directions: Record<string, { x: number; y: number }> = {
    ArrowLeft: { x: -1, y: 0 },
    ArrowRight: { x: 1, y: 0 },
    ArrowUp: { x: 0, y: -1 },
    ArrowDown: { x: 0, y: 1 },
  };
  const direction = directions[event.key];
  if (!direction) return;
  const current = focusTargets.find((target) => target.id === currentId);
  if (!current) return;

  const next = focusTargets
    .filter((target) => {
      const dx = target.x - current.x;
      const dy = target.y - current.y;
      return direction.x ? dx * direction.x > 0 : dy * direction.y > 0;
    })
    .map((target) => {
      const dx = target.x - current.x;
      const dy = target.y - current.y;
      const forward = Math.abs(direction.x ? dx : dy);
      const perpendicular = Math.abs(direction.x ? dy : dx);
      return { target, score: forward + perpendicular * 2.4 };
    })
    .sort((a, b) => a.score - b.score)[0]?.target;

  if (!next) return;
  event.preventDefault();
  focusId.value = next.id;
  void nextTick(() => {
    stage.value?.querySelector<HTMLElement>(`[data-focus-id="${next.id}"]`)?.focus();
  });
}

function updateScale(): void {
  const element = stage.value;
  if (!element) return;
  const widthScale = Math.max(0.25, (element.clientWidth - 20) / KEYBOARD_BOUNDS.width);
  const heightScale = Math.max(0.25, (element.clientHeight - 68) / KEYBOARD_BOUNDS.height);
  scale.value = Math.min(0.68, widthScale, heightScale);
}

onMounted(() => {
  resizeObserver = new ResizeObserver(updateScale);
  if (stage.value) resizeObserver.observe(stage.value);
  updateScale();
});

onBeforeUnmount(() => resizeObserver?.disconnect());

watch(
  () => props.selectedIds,
  (selectedIds) => {
    const selected = selectedIds[selectedIds.length - 1];
    if (selected) focusId.value = physicalControlId(selected);
  },
  { deep: true },
);
</script>

<template>
  <div ref="stage" class="keyboard-stage" @click="clearFromSurface">
    <div class="keyboard-map" :style="rootStyle" role="group" aria-label="AK Ergo 77 分体磁轴键盘">
      <div
        v-for="chassis in KEYBOARD_HARDWARE.chassis"
        :key="chassis.side"
        class="keyboard-chassis keyboard-chassis--whole"
        :style="rectStyle(chassis)"
        aria-hidden="true"
      ></div>

      <div
        class="hardware-screen"
        :style="rectStyle(KEYBOARD_HARDWARE.screen)"
        role="img"
        aria-label="键盘屏幕预览：Factory Default"
      >
        <span class="hardware-screen__profile">FACTORY / 01</span>
        <span class="screen-badge">PROFILE</span>
        <span class="screen-wave" aria-hidden="true"></span>
        <span class="hardware-screen__status">RT 40% &nbsp;&nbsp;&nbsp; 8K</span>
      </div>

      <button
        type="button"
        class="hardware-control fiveway"
        :class="{ selected: isHardwareSelected('fiveway_000'), 'is-dirty': isHardwareDirty('fiveway_000') }"
        :style="rectStyle(KEYBOARD_HARDWARE.fiveway)"
        data-focus-id="fiveway_000"
        :tabindex="focusId === 'fiveway_000' ? 0 : -1"
        aria-label="选择五向摇杆按压事件"
        :aria-pressed="isHardwareSelected('fiveway_000')"
        @focus="focusId = 'fiveway_000'"
        @keydown="navigateFocus('fiveway_000', $event)"
        @click="select('fiveway_000.press', $event)"
      >
        <span class="hardware-caption">FIVEWAY / 7 EVENTS</span>
      </button>
      <button
        type="button"
        class="hardware-control encoder"
        :class="{ selected: isHardwareSelected('enc_000'), 'is-dirty': isHardwareDirty('enc_000') }"
        :style="rectStyle(KEYBOARD_HARDWARE.encoder)"
        data-focus-id="enc_000"
        :tabindex="focusId === 'enc_000' ? 0 : -1"
        aria-label="选择 EC11 编码器按压事件"
        :aria-pressed="isHardwareSelected('enc_000')"
        @focus="focusId = 'enc_000'"
        @keydown="navigateFocus('enc_000', $event)"
        @click="select('enc_000.press', $event)"
      >
        <span class="hardware-caption">EC11 / 3 EVENTS</span>
      </button>

      <button
        v-for="key in KEYBOARD_KEYS"
        :key="key.id"
        type="button"
        class="keycap"
        :class="{
          selected: isSelected(key.id),
          'no-op': key.noOp,
          'is-dirty': dirtyIds?.includes(key.id),
        }"
        :style="keyStyle(key)"
        :data-focus-id="key.id"
        :tabindex="focusId === key.id ? 0 : -1"
        :aria-label="`${key.label}, ${key.id}`"
        :aria-pressed="isSelected(key.id)"
        @focus="focusId = key.id"
        @keydown="navigateFocus(key.id, $event)"
        @click="select(key.id, $event)"
      >
        <span class="keycap-light" aria-hidden="true"></span>
        <span class="keycap-heat" aria-hidden="true"></span>
        <span class="keycap-label">{{ key.label }}</span>
      </button>
    </div>
  </div>
</template>
