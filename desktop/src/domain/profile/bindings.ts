export const BINDING_CATEGORY_LABELS = {
  keyboard: "键盘",
  consumer: "媒体",
  mouse: "鼠标",
  behavior: "行为",
  macro: "宏",
  no_op: "无操作",
} as const;

const FRIENDLY_NAMES: Record<string, string> = {
  no_op: "无操作",
  "keyboard.escape": "Esc",
  "keyboard.space": "空格",
  "keyboard.enter": "回车",
  "keyboard.tab": "Tab",
  "keyboard.backspace": "退格",
  "keyboard.caps_lock": "Caps Lock",
  "keyboard.left_shift": "左 Shift",
  "keyboard.right_shift": "右 Shift",
  "keyboard.left_ctrl": "左 Ctrl",
  "keyboard.right_ctrl": "右 Ctrl",
  "keyboard.left_alt": "左 Alt",
  "keyboard.right_alt": "右 Alt",
  "keyboard.left_gui": "左 Win",
  "keyboard.right_gui": "右 Win",
  "consumer.volume_increment": "音量增加",
  "consumer.volume_decrement": "音量降低",
  "consumer.mute": "静音",
  "mouse.wheel_up": "滚轮向上",
  "mouse.wheel_down": "滚轮向下",
};

export function bindingCategory(binding: string): keyof typeof BINDING_CATEGORY_LABELS {
  if (binding === "no_op") return "no_op";
  const prefix = binding.split(".", 1)[0];
  return prefix in BINDING_CATEGORY_LABELS
    ? (prefix as keyof typeof BINDING_CATEGORY_LABELS)
    : "behavior";
}

export function bindingLabel(binding: string): string {
  const friendly = FRIENDLY_NAMES[binding];
  if (friendly) return friendly;
  const [, value = binding] = binding.split(".", 2);
  if (/^[a-z]$/i.test(value)) return `字母 ${value.toUpperCase()}`;
  if (/^\d$/.test(value)) return `数字 ${value}`;
  if (/^f\d{1,2}$/i.test(value)) return value.toUpperCase();
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter: string) => letter.toUpperCase());
}

export function bindingDescription(binding: string): string {
  const category = BINDING_CATEGORY_LABELS[bindingCategory(binding)];
  return `${category}输出 · ${binding === "no_op" ? "不生成 Host 事件" : "直接 Host 输入"}`;
}
