<script setup lang="ts">
import { computed, ref } from "vue";
import KeyboardBoard from "../components/keyboard/KeyboardBoard.vue";
import { getKeyboardKey, KEYBOARD_KEYS } from "../domain/keyboard/layout";

type DiagnosticTab = "live" | "baseline" | "history";

const selectedIds = ref<string[]>([]);
const diagnosticTab = ref<DiagnosticTab>("live");
const activeGroup = ref("");
const protectFactoryBaseline = ref(true);
const localMessage = ref("已载入 CAD 配列，尚未请求设备数据");

const primaryId = computed(() => selectedIds.value[selectedIds.value.length - 1] ?? "");
const primaryKey = computed(() => getKeyboardKey(primaryId.value));
const primaryLabel = computed(() => selectedIds.value.length ? primaryKey.value?.label ?? "硬件控件" : "未选择控件");
const primaryMeta = computed(() => {
  if (!selectedIds.value.length) return "选择键帽后查看对应的诊断入口";
  if (!primaryKey.value) return "非磁轴控件 · 暂无校准数据接口";
  return `${primaryKey.value.side === "left" ? "左半区" : "右半区"} · 磁轴键 · CAD 定位`;
});

function selectControl(payload: { id: string; additive: boolean; range: boolean }): void {
  if (payload.additive) {
    selectedIds.value = selectedIds.value.includes(payload.id)
      ? selectedIds.value.filter((id) => id !== payload.id)
      : [...selectedIds.value, payload.id];
  } else {
    selectedIds.value = [payload.id];
  }
  localMessage.value = `已在 CAD 配列中选择 ${payload.id}；未读取实时传感数据`;
}

function chooseGroup(group: string): void {
  activeGroup.value = group;
  if (group === "左半区") selectedIds.value = KEYBOARD_KEYS.filter((key) => key.side === "left").map((key) => key.id);
  if (group === "右半区") selectedIds.value = KEYBOARD_KEYS.filter((key) => key.side === "right").map((key) => key.id);
  if (group === "WASD") selectedIds.value = ["key_058", "key_065", "key_064", "key_063"];
  localMessage.value = `已在本地选择${group}；采样协议尚未接入`;
}

function clearSelection(): void {
  selectedIds.value = [];
  activeGroup.value = "";
  localMessage.value = "已清除本地选择";
}
</script>

<template>
  <div class="page-shell">
    <section class="page-main calibration-main">
      <header class="page-heading">
        <div>
          <p class="page-kicker">CALIBRATION DATA / DIAGNOSTICS</p>
          <h1>校准与诊断</h1>
          <p class="lede">先用真实 CAD 配列完成定位与选择；实时 ADC、行程和校准命令要等设备传感数据协议接入。</p>
        </div>
        <div class="heading-actions">
          <span class="chip amber"><i></i>实时协议未接入</span>
          <button class="button primary" disabled title="设备实时校准协议尚未接入">开始全键校准</button>
        </div>
      </header>

      <div class="calibration-summary">
        <span><b>{{ KEYBOARD_KEYS.length }}</b><small>CAD 磁轴键</small></span>
        <span><b>—</b><small>行程一致性</small></span>
        <span><b>0</b><small>实时样本</small></span>
        <em>设备校准记录尚未读取</em>
      </div>

      <section class="keyboard-surface">
        <KeyboardBoard :selected-ids="selectedIds" @select="selectControl" @clear="clearSelection" />
        <div class="keyboard-toolbar">
          <span><kbd>Click</kbd>单选　<kbd>Ctrl</kbd>多选　当前仅定位控件</span>
          <div class="key-groups">
            <button v-for="group in ['WASD', '左半区', '右半区']" :key="group" :class="{ active: activeGroup === group }" @click="chooseGroup(group)">{{ group }}</button>
          </div>
          <span class="selection-count">{{ selectedIds.length ? `${selectedIds.length} 个控件` : "未选择" }}</span>
        </div>
      </section>

      <div class="status-strip">
        <div class="status-item">
          <i class="status-icon">CAD</i>
          <p><b>物理配列已载入</b><small>{{ localMessage }}</small></p>
        </div>
        <div class="status-item">
          <i class="status-icon">—</i>
          <p><b>校准数据不可用</b><small>未伪造在线、采样或健康状态</small></p>
        </div>
        <button class="status-link" @click="diagnosticTab = 'baseline'">查看待接入字段 →</button>
      </div>
    </section>

    <aside class="page-inspector">
      <div class="inspector-inner">
        <header class="inspector-head">
          <p class="tiny-label">当前选择</p>
          <h2>{{ primaryLabel }} <small v-if="primaryId">{{ primaryId }}</small></h2>
          <p>{{ primaryMeta }} · 未进行实时采样</p>
        </header>
        <nav class="inspector-tabs" role="tablist" aria-label="诊断数据页签">
          <button role="tab" :aria-selected="diagnosticTab === 'live'" :class="{ active: diagnosticTab === 'live' }" @click="diagnosticTab = 'live'">实时</button>
          <button role="tab" :aria-selected="diagnosticTab === 'baseline'" :class="{ active: diagnosticTab === 'baseline' }" @click="diagnosticTab = 'baseline'">基线</button>
          <button role="tab" :aria-selected="diagnosticTab === 'history'" :class="{ active: diagnosticTab === 'history' }" @click="diagnosticTab = 'history'">历史</button>
        </nav>

        <div class="inspector-scroll">
          <template v-if="diagnosticTab === 'live'">
            <div v-if="!selectedIds.length" class="selection-empty" role="status">
              <div class="selection-empty__key" aria-hidden="true"><span></span></div>
              <p class="tiny-label">LIVE SENSOR TARGET</p>
              <h3>选择要观察的按键</h3>
              <p>基线和历史页可以直接浏览；实时行程需要先在左侧指定一个或多个键。</p>
            </div>
            <template v-else>
              <section class="inspector-section">
                <div class="section-title"><h3>实时行程</h3><span class="chip amber"><i></i>未采样</span></div>
                <div class="calibration-readout">
                  <div><span>RAW ADC</span><b>—</b></div>
                  <div><span>归一化</span><b>—</b></div>
                  <div><span>估算行程</span><b>—</b></div>
                </div>
                <div class="graph-panel calibration-graph">
                  <span class="graph-label tl">等待样本</span><span class="graph-label tr">OFFLINE</span><span class="graph-label bl">0 mm</span>
                  <svg viewBox="0 0 300 110" fill="none" aria-hidden="true">
                    <path d="M12 78 H288" stroke="#cbd1d9" stroke-width="2" stroke-dasharray="5 7" />
                  </svg>
                </div>
              </section>
              <section class="inspector-section"><div class="notice">实时传感数据协议尚未接入，因此这里不会显示模拟 ADC 或行程曲线。</div></section>
            </template>
          </template>

          <template v-else-if="diagnosticTab === 'baseline'">
            <section class="inspector-section">
              <div class="section-title"><h3>校准基线</h3><span>factory + user</span></div>
              <div class="profile-meta-grid">
                <span>零点</span><b>未读取</b>
                <span>满程</span><b>未读取</b>
                <span>噪声</span><b>未读取</b>
                <span>用户偏移</span><b>未读取</b>
              </div>
            </section>
            <section class="inspector-section">
              <div class="toggle-line" role="button" tabindex="0" @click="protectFactoryBaseline = !protectFactoryBaseline; localMessage = '保护开关仅为本地界面预览，尚未写入设备'" @keydown.enter="protectFactoryBaseline = !protectFactoryBaseline; localMessage = '保护开关仅为本地界面预览，尚未写入设备'" @keydown.space.prevent="protectFactoryBaseline = !protectFactoryBaseline; localMessage = '保护开关仅为本地界面预览，尚未写入设备'">
                <span><b>保护出厂校准副本</b><small>本地预览开关，尚未持久化</small></span>
                <i class="switch" :class="{ on: protectFactoryBaseline }"></i>
              </div>
            </section>
            <section class="inspector-section"><div class="notice warning">需先定义读取 factory / user baseline 的桥接方法，再开放偏移编辑与校准按钮。</div></section>
          </template>

          <template v-else>
            <section class="inspector-section">
              <div class="section-title"><h3>校准历史</h3><span>0 条已载入</span></div>
              <div class="advanced-choice"><b>尚无设备记录</b><small>历史查询协议与数据结构待接入</small></div>
            </section>
            <section class="inspector-section"><div class="notice">接入后这里将展示真实时间、固件版本、基线来源和异常键，不使用示例记录冒充设备数据。</div></section>
          </template>
        </div>

        <div class="inspector-actions">
          <button class="button quiet" :disabled="!selectedIds.length" @click="clearSelection">清除本地选择</button>
          <button class="button primary" disabled title="实时校准协议尚未接入">校准所选键</button>
        </div>
      </div>
    </aside>
  </div>
</template>
