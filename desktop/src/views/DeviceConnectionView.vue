<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useDeviceStore } from "../stores/device";
import { useProfileStore } from "../stores/profile";

type InspectorTab = "connection" | "input" | "maintenance";

interface RateOption {
  label: string;
  value: number;
}

const device = useDeviceStore();
const profile = useProfileStore();
const selectedPort = ref("");
const inspectorTab = ref<InspectorTab>("connection");
const wiredRateOptions: RateOption[] = [
  { label: "1K", value: 1000 },
  { label: "2K", value: 2000 },
  { label: "4K", value: 4000 },
  { label: "8K", value: 8000 },
];
const wirelessRateOptions: RateOption[] = [
  { label: "125", value: 125 },
  { label: "250", value: 250 },
  { label: "500", value: 500 },
  { label: "1K", value: 1000 },
];
const wiredRateHz = ref(profile.draftDocument.report_rate_policy.usb_report_rate_hz);
const wirelessRateHz = ref(profile.draftDocument.report_rate_policy.ch585_wireless_report_rate_hz);
const lockWindowsKey = ref(profile.draftDocument.input_guard_policy.win_key_lock_enabled);
const policyStatus = ref("当前值来自 Profile 草稿；固件支持尚未接入");

const activeProfileLabel = computed(() =>
  device.deviceActiveSlot === 0 ? "Factory Profile" : `用户 Slot ${device.deviceActiveSlot}`,
);

const profileIdLabel = computed(() =>
  device.profileId16 === null
    ? "—"
    : `0x${device.profileId16.toString(16).padStart(4, "0")}`,
);

const connectionMeta = computed(() => {
  if (!device.connected) return "等待选择 H417 USB CDC 端口";
  return `${device.port} · ${activeProfileLabel.value} · Gen ${device.generation ?? "—"}`;
});

const policyFormDirty = computed(() =>
  wiredRateHz.value !== profile.draftDocument.report_rate_policy.usb_report_rate_hz
  || wirelessRateHz.value !== profile.draftDocument.report_rate_policy.ch585_wireless_report_rate_hz
  || lockWindowsKey.value !== profile.draftDocument.input_guard_policy.win_key_lock_enabled,
);

const profilePolicyDirty = computed(() =>
  JSON.stringify(profile.draftDocument.report_rate_policy)
    !== JSON.stringify(profile.sourceDocument.report_rate_policy)
  || JSON.stringify(profile.draftDocument.input_guard_policy)
    !== JSON.stringify(profile.sourceDocument.input_guard_policy),
);

const policyStateLabel = computed(() => {
  if (policyFormDirty.value) return "本页有未保存修改";
  if (profilePolicyDirty.value) return "已保存到 Profile 草稿";
  return "与 Profile 来源一致";
});

watch(
  () => device.ports,
  (ports) => {
    if (!ports.some((port) => port.device === selectedPort.value)) {
      selectedPort.value = ports[0]?.device ?? "";
    }
  },
  { deep: true },
);

async function refreshPorts(): Promise<void> {
  await device.refreshPorts();
}

async function connectSelected(): Promise<void> {
  if (!selectedPort.value) return;
  await device.connect(selectedPort.value);
}

function markPolicyFormChanged(): void {
  policyStatus.value = "参数只在本页改变；点击保存后才会写入 Profile 草稿";
}

function selectWiredRate(rate: number): void {
  wiredRateHz.value = rate;
  markPolicyFormChanged();
}

function selectWirelessRate(rate: number): void {
  wirelessRateHz.value = rate;
  markPolicyFormChanged();
}

function toggleWindowsKeyLock(): void {
  lockWindowsKey.value = !lockWindowsKey.value;
  markPolicyFormChanged();
}

function discardPolicyForm(): void {
  wiredRateHz.value = profile.draftDocument.report_rate_policy.usb_report_rate_hz;
  wirelessRateHz.value = profile.draftDocument.report_rate_policy.ch585_wireless_report_rate_hz;
  lockWindowsKey.value = profile.draftDocument.input_guard_policy.win_key_lock_enabled;
  policyStatus.value = "已放弃本页尚未保存的修改";
}

function savePolicyForm(): void {
  if (!policyFormDirty.value) return;
  profile.draftDocument.report_rate_policy = {
    ...profile.draftDocument.report_rate_policy,
    usb_report_rate_hz: wiredRateHz.value,
    ch585_wireless_report_rate_hz: wirelessRateHz.value,
  };
  profile.draftDocument.input_guard_policy = {
    ...profile.draftDocument.input_guard_policy,
    win_key_lock_enabled: lockWindowsKey.value,
  };
  profile.compileState = "idle";
  profile.compileMessage = "输入策略已保存到 Profile 草稿，等待重新验证";
  policyStatus.value = "字段已写入 Profile 草稿；当前固件不会应用这些参数";
}

onMounted(() => void refreshPorts());
</script>

<template>
  <div class="page-shell">
    <section class="page-main device-main">
      <header class="page-heading">
        <div>
          <p class="page-kicker">DEVICE SETTINGS / CONNECTION</p>
          <h1>设备与连接</h1>
          <p class="lede">连接事实属于设备层；Profile 草稿不会伪装成在线状态。</p>
        </div>
        <div class="heading-actions">
          <button class="button quiet" :disabled="device.state === 'connecting'" @click="refreshPorts">
            刷新端口
          </button>
          <button v-if="device.connected" class="button primary" @click="device.disconnect">断开连接</button>
          <button v-else class="button primary" :disabled="!selectedPort || device.state === 'connecting'" @click="connectSelected">
            {{ device.state === "connecting" ? "正在连接" : "连接设备" }}
          </button>
        </div>
      </header>

      <section class="device-workspace">
        <div class="device-hero">
          <div class="device-outline" aria-hidden="true">
            <div class="mini-chassis"></div>
            <div class="mini-board left"></div><div class="mini-board right"></div>
            <div class="mini-screen"><span>KIIIe</span><b>{{ device.connected ? "ONLINE" : "STANDBY" }}</b></div>
            <i class="mini-dial"></i><i class="mini-encoder"></i>
          </div>
          <div class="device-title">
            <span class="chip" :class="device.connected ? 'green' : ''"><i></i>{{ device.connected ? "当前设备" : "尚未连接" }}</span>
            <h2>AK Ergo 77</h2>
            <p>{{ connectionMeta }}</p>
          </div>
          <div class="device-health">
            <div><span>当前运行</span><b>{{ activeProfileLabel }}</b><i :style="{ '--health': device.connected ? '100%' : '0%' }"></i></div>
            <div><span>Profile ID</span><b>{{ profileIdLabel }}</b><i :style="{ '--health': device.profileId16 === null ? '0%' : '100%' }"></i></div>
            <div><span>可用用户槽</span><b>{{ device.slotValid.filter(Boolean).length }} / 3</b><i :style="{ '--health': `${device.slotValid.filter(Boolean).length * 33.33}%` }"></i></div>
          </div>
        </div>

        <div class="connection-panel">
          <header><span class="tiny-label">USB CDC PORTS</span><b>{{ device.ports.length }} 个候选端口</b></header>
          <div class="connection-track">
            <article :class="{ active: device.connected }">
              <i class="transport-icon usb">USB</i>
              <div>
                <b>H417 有线配置通道</b>
                <small>{{ device.connected ? `${device.port} · 已建立协议连接` : "选择由 H417 枚举的 CDC 端口" }}</small>
              </div>
              <span class="chip" :class="device.connected ? 'green' : ''"><i></i>{{ device.connected ? "已连接" : "离线" }}</span>
            </article>
            <label class="select-field binding-select-field" for="serial-port">
              <i class="field-icon">COM</i>
              <span><strong>{{ selectedPort || "未发现端口" }}</strong><small>不会自动尝试连接</small></span>
              <select id="serial-port" v-model="selectedPort" :disabled="device.connected || !device.ports.length">
                <option value="" disabled>选择串口</option>
                <option v-for="port in device.ports" :key="port.device" :value="port.device">
                  {{ port.device }} · {{ port.description || port.name || "Serial port" }}
                </option>
              </select>
              <i class="field-chevron">⌄</i>
            </label>
            <article aria-disabled="true">
              <i class="transport-icon radio">2.4</i><div><b>2.4 GHz 接收器</b><small>配置协议尚未接入桌面 Core</small></div><span class="chip">待接入</span>
            </article>
            <article aria-disabled="true">
              <i class="transport-icon bt">BT</i><div><b>Bluetooth</b><small>配置协议尚未接入桌面 Core</small></div><span class="chip">待接入</span>
            </article>
          </div>
          <footer><span aria-live="polite">{{ device.lastError || "端口枚举不会打开设备" }}</span><button @click="refreshPorts">重新扫描 →</button></footer>
        </div>

        <div class="device-actions">
          <button disabled><i>↻</i><span><b>重新启动设备</b><small>维护命令待接入</small></span></button>
          <button disabled><i>⇧</i><span><b>进入固件更新</b><small>DFU 流程待接入</small></span></button>
          <button class="danger" disabled><i>!</i><span><b>恢复出厂设置</b><small>需要硬件确认机制</small></span></button>
        </div>
      </section>
    </section>

    <aside class="page-inspector">
      <div class="inspector-inner">
        <header class="inspector-head">
          <p class="tiny-label">设备设置</p>
          <h2>连接与输入策略 <small>DEVICE / PROFILE</small></h2>
          <p>连接属于设备；回报率与输入保护保存到当前 Profile。</p>
        </header>
        <nav class="inspector-tabs" role="tablist" aria-label="设备设置类型">
          <button role="tab" :aria-selected="inspectorTab === 'connection'" :class="{ active: inspectorTab === 'connection' }" @click="inspectorTab = 'connection'">连接</button>
          <button role="tab" :aria-selected="inspectorTab === 'input'" :class="{ active: inspectorTab === 'input' }" @click="inspectorTab = 'input'">输入</button>
          <button role="tab" :aria-selected="inspectorTab === 'maintenance'" :class="{ active: inspectorTab === 'maintenance' }" @click="inspectorTab = 'maintenance'">维护</button>
        </nav>
        <div class="inspector-scroll">
          <template v-if="inspectorTab === 'connection'">
            <section class="inspector-section">
              <div class="section-title"><h3>Profile 槽位</h3><span>1–3 可写</span></div>
              <div class="mode-list">
                <button
                  v-for="slot in [1, 2, 3]"
                  :key="slot"
                  class="mode-row"
                  :class="{ active: device.targetUserSlot === slot }"
                  @click="device.targetUserSlot = slot"
                >
                  <span><b>用户 Slot {{ slot }}</b><small>{{ device.slotValid[slot - 1] ? "设备中已有 Profile" : "当前为空" }}</small></span>
                  <i class="radio" :class="{ active: device.targetUserSlot === slot }"></i>
                </button>
              </div>
            </section>
            <section class="inspector-section"><div class="notice">Factory Slot 0 只读。写入按钮始终以这里选择的用户槽为目标。</div></section>
          </template>

          <template v-else-if="inspectorTab === 'input'">
            <section class="inspector-section">
              <div class="section-title"><h3>Profile 输入策略</h3><span class="chip amber">等待固件支持</span></div>
              <div class="notice warning">这些字段会真实保存到 Profile JSON，但当前固件尚不会应用。固件接入后无需重做配置界面。</div>
            </section>
            <section class="inspector-section">
              <div class="section-title"><h3>USB 有线回报率</h3><span>{{ wiredRateHz }} Hz</span></div>
              <div class="segmented rate-picker">
                <button
                  v-for="rate in wiredRateOptions"
                  :key="rate.value"
                  :class="{ active: wiredRateHz === rate.value }"
                  @click="selectWiredRate(rate.value)"
                >{{ rate.label }}</button>
              </div>
              <div class="section-title second-label"><h3>CH585 无线回报率</h3><span>{{ wirelessRateHz }} Hz</span></div>
              <div class="segmented rate-picker">
                <button
                  v-for="rate in wirelessRateOptions"
                  :key="rate.value"
                  :class="{ active: wirelessRateHz === rate.value }"
                  @click="selectWirelessRate(rate.value)"
                >{{ rate.label }}</button>
              </div>
            </section>
            <section class="inspector-section">
              <button
                class="toggle-line profile-policy-toggle"
                role="switch"
                :aria-checked="lockWindowsKey"
                @click="toggleWindowsKeyLock"
              >
                <span><b>锁定 Win 键输出</b><small>阻止 left_gui / right_gui · 当前 Profile</small></span>
                <i class="switch" :class="{ on: lockWindowsKey }"></i>
              </button>
              <p class="policy-status" aria-live="polite"><i></i><span><b>{{ policyStateLabel }}</b><small>{{ policyStatus }}</small></span></p>
            </section>
          </template>

          <template v-else>
            <section class="inspector-section"><div class="notice warning">维护命令尚未并入当前桌面 bridge，因此本页不会发送重启、DFU 或恢复出厂指令。</div></section>
          </template>
        </div>
        <div class="inspector-actions">
          <template v-if="inspectorTab === 'input'">
            <button class="button quiet" :disabled="!policyFormDirty" @click="discardPolicyForm">放弃本页修改</button>
            <button class="button primary" :disabled="!policyFormDirty" @click="savePolicyForm">保存到 Profile</button>
          </template>
          <template v-else>
            <button class="button quiet" disabled>恢复设备默认</button>
            <button class="button primary" disabled>保存设备设置</button>
          </template>
        </div>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.profile-policy-toggle {
  width: 100%;
  padding: 0;
  color: var(--ink-soft);
  background: transparent;
  text-align: left;
}

.policy-status {
  display: grid;
  grid-template-columns: 7px minmax(0, 1fr);
  align-items: center;
  gap: 9px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}

.policy-status > i {
  width: 6px;
  height: 22px;
  border-radius: 999px;
  background: var(--accent);
}

.policy-status > span {
  display: grid;
  gap: 3px;
}

.policy-status b {
  font-size: 9px;
}

.policy-status small {
  color: var(--muted);
  font-size: 8px;
  line-height: 1.35;
}
</style>
