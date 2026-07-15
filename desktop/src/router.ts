import { createRouter, createWebHashHistory } from "vue-router";
import AdvancedBehaviorView from "./views/AdvancedBehaviorView.vue";
import DeviceConnectionView from "./views/DeviceConnectionView.vue";
import DiagnosticsCalibrationView from "./views/DiagnosticsCalibrationView.vue";
import DisplayEditorView from "./views/DisplayEditorView.vue";
import KeymapMappingView from "./views/KeymapMappingView.vue";
import LightingStudioView from "./views/LightingStudioView.vue";
import MacroEditorView from "./views/MacroEditorView.vue";
import ProfileLibraryView from "./views/ProfileLibraryView.vue";

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", redirect: "/keymap" },
    {
      path: "/keymap",
      name: "keymap",
      component: KeymapMappingView,
      meta: { inspector: true, layout: "canvas", title: "键位与触发" },
    },
    {
      path: "/behavior",
      name: "behavior",
      component: AdvancedBehaviorView,
      meta: { inspector: true, layout: "canvas", title: "高级行为" },
    },
    {
      path: "/macro",
      name: "macro",
      component: MacroEditorView,
      meta: { inspector: true, layout: "editor", title: "宏与规则" },
    },
    {
      path: "/lighting",
      name: "lighting",
      component: LightingStudioView,
      meta: { inspector: true, layout: "studio", title: "灯效工作室" },
    },
    {
      path: "/display",
      name: "display",
      component: DisplayEditorView,
      meta: { inspector: true, layout: "studio", title: "屏显编辑器" },
    },
    {
      path: "/profiles",
      name: "profiles",
      component: ProfileLibraryView,
      meta: { inspector: true, layout: "editor", title: "Profile 库" },
    },
    {
      path: "/diagnostics",
      name: "diagnostics",
      component: DiagnosticsCalibrationView,
      meta: { inspector: true, layout: "canvas", title: "校准与诊断" },
    },
    {
      path: "/device",
      name: "device",
      component: DeviceConnectionView,
      meta: { inspector: true, layout: "settings", title: "设备与连接" },
    },
  ],
});

router.afterEach((to) => {
  document.title = `${String(to.meta.title ?? "Control Lab")} · KIIIe Control Lab`;
});

export default router;
