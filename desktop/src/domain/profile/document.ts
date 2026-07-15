import type { ProfileDocument } from "./types";

export function cloneProfileDocument(document: ProfileDocument): ProfileDocument {
  return JSON.parse(JSON.stringify(document)) as ProfileDocument;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stableSignature(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableSignature).join(",")}]`;
  if (isPlainObject(value)) {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableSignature(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value) ?? "undefined";
}

export function isProfileDocument(value: unknown): value is ProfileDocument {
  if (!isPlainObject(value)) return false;
  const identity = value.identity;
  const coreShape = isPlainObject(identity)
    && typeof identity.profile_id === "string"
    && typeof identity.name === "string"
    && isPlainObject(value.compatibility)
    && isPlainObject(value.defaults)
    && Array.isArray(value.control_assignments)
    && isPlainObject(value.binding_scopes)
    && Object.keys(value.binding_scopes).length > 0;
  if (!coreShape) return false;
  try {
    const document = value as unknown as ProfileDocument;
    validateTriggerDefaults(document);
    validateControlAssignments(document);
    validateCollections(document);
    return true;
  } catch {
    return false;
  }
}

function validateControlAssignments(document: ProfileDocument): void {
  const numericParams = new Set([
    "press_threshold_norm_i16", "release_threshold_norm_i16", "reset_threshold_norm_i16",
    "press_delta_norm_i16", "release_delta_norm_i16", "deadzone_norm_i16",
  ]);
  document.control_assignments.forEach((value, index) => {
    if (!isPlainObject(value)) throw new Error(`control_assignments[${index}] 必须是对象`);
    if (!Array.isArray(value.controls) || !value.controls.every((control) => typeof control === "string")) {
      throw new Error(`control_assignments[${index}].controls 必须是字符串数组`);
    }
    if (value.type !== "akey") return;
    if (value.mode !== "normal" && value.mode !== "rapid_trigger" && value.mode !== "disabled") {
      throw new Error(`control_assignments[${index}].mode 不受支持`);
    }
    if (!isPlainObject(value.params)) throw new Error(`control_assignments[${index}].params 必须是对象`);
    for (const [key, parameter] of Object.entries(value.params)) {
      if (numericParams.has(key)
        && (!Number.isInteger(parameter) || (parameter as number) < 0 || (parameter as number) > 1000)) {
        throw new Error(`control_assignments[${index}].params.${key} 必须是 0–1000 的整数`);
      }
    }
    if ("defaults" in value.params && typeof value.params.defaults !== "boolean") {
      throw new Error(`control_assignments[${index}].params.defaults 必须是布尔值`);
    }
    if ("retrigger_before_reset" in value.params
      && typeof value.params.retrigger_before_reset !== "boolean") {
      throw new Error(`control_assignments[${index}].params.retrigger_before_reset 必须是布尔值`);
    }
  });
}

function validateCollections(document: ProfileDocument): void {
  for (const [scopeId, value] of Object.entries(document.binding_scopes)) {
    if (!isPlainObject(value)
      || !isPlainObject(value.bindings)
      || !Object.values(value.bindings).every((target) => typeof target === "string")
      || typeof value.unbound !== "string") {
      throw new Error(`binding_scopes.${scopeId} 结构无效`);
    }
  }
  const raw = document as ProfileDocument & {
    behaviors?: unknown;
    interaction_rules?: unknown;
    macro_defs?: unknown;
  };
  if (raw.behaviors !== undefined && (!isPlainObject(raw.behaviors)
    || !Object.values(raw.behaviors).every((behavior) => isPlainObject(behavior) && typeof behavior.kind === "string"))) {
    throw new Error("behaviors 必须是以 behavior id 为 key 的对象");
  }
  if (raw.interaction_rules !== undefined && (!Array.isArray(raw.interaction_rules)
    || !raw.interaction_rules.every((rule) => isPlainObject(rule)
      && typeof rule.rule_id === "string"
      && typeof rule.kind === "string"))) {
    throw new Error("interaction_rules 必须是规则对象数组");
  }
  if (raw.macro_defs !== undefined) {
    if (!isPlainObject(raw.macro_defs)) throw new Error("macro_defs 必须是对象");
    for (const [macroId, value] of Object.entries(raw.macro_defs)) {
      if (!isPlainObject(value)
        || !Array.isArray(value.steps)
        || value.repeat !== "none"
        || typeof value.cancel_on_release !== "boolean") {
        throw new Error(`macro_defs.${macroId} 结构无效`);
      }
      value.steps.forEach((step, index) => {
        if (!isPlainObject(step) || typeof step.op !== "string") {
          throw new Error(`macro_defs.${macroId}.steps[${index}] 结构无效`);
        }
        const stepRecord = step as unknown as Record<string, unknown>;
        if (["key_down", "key_up", "consumer_down", "consumer_up"].includes(step.op)
          && typeof stepRecord.usage !== "string") {
          throw new Error(`macro_defs.${macroId}.steps[${index}].usage 必须是字符串`);
        }
        if (step.op === "pointer_step"
          && (!Number.isInteger(stepRecord.dx) || !Number.isInteger(stepRecord.dy) || !Number.isInteger(stepRecord.wheel))) {
          throw new Error(`macro_defs.${macroId}.steps[${index}] 的指针参数必须是整数`);
        }
        if (step.op === "delay_ticks" && (!Number.isInteger(stepRecord.ticks) || (stepRecord.ticks as number) < 0)) {
          throw new Error(`macro_defs.${macroId}.steps[${index}].ticks 必须是非负整数`);
        }
        if (step.op === "wait_release" && typeof stepRecord.source !== "string") {
          throw new Error(`macro_defs.${macroId}.steps[${index}].source 必须是字符串`);
        }
        if (!["key_down", "key_up", "consumer_down", "consumer_up", "pointer_step", "delay_ticks", "wait_release", "stop"].includes(step.op)) {
          throw new Error(`macro_defs.${macroId}.steps[${index}].op 不受支持`);
        }
      });
    }
  }
}

function requiredInteger(value: unknown, path: string): number {
  if (!Number.isInteger(value)) throw new Error(`Profile 字段 ${path} 必须是整数`);
  return value as number;
}

function validateTriggerDefaults(document: ProfileDocument): void {
  const akey = document.defaults.triggers?.akey;
  if (!isPlainObject(akey?.common) || !isPlainObject(akey?.normal) || !isPlainObject(akey?.rapid_trigger)) {
    throw new Error("Profile 缺少 defaults.triggers.akey 的 common / normal / rapid_trigger 默认参数");
  }
  requiredInteger(akey.common.deadzone_norm_i16, "defaults.triggers.akey.common.deadzone_norm_i16");
  requiredInteger(akey.normal.press_threshold_norm_i16, "defaults.triggers.akey.normal.press_threshold_norm_i16");
  requiredInteger(akey.normal.release_threshold_norm_i16, "defaults.triggers.akey.normal.release_threshold_norm_i16");
  requiredInteger(akey.rapid_trigger.reset_threshold_norm_i16, "defaults.triggers.akey.rapid_trigger.reset_threshold_norm_i16");
  requiredInteger(akey.rapid_trigger.release_delta_norm_i16, "defaults.triggers.akey.rapid_trigger.release_delta_norm_i16");
  requiredInteger(akey.rapid_trigger.press_delta_norm_i16, "defaults.triggers.akey.rapid_trigger.press_delta_norm_i16");
  if (typeof akey.rapid_trigger.retrigger_before_reset !== "boolean") {
    throw new Error("Profile 字段 defaults.triggers.akey.rapid_trigger.retrigger_before_reset 必须是布尔值");
  }
}

export function normalizeProfileDocument(source: ProfileDocument): ProfileDocument {
  const document = cloneProfileDocument(source);

  if (!isProfileDocument(document)) throw new Error("Profile 缺少必需的核心结构");
  validateTriggerDefaults(document);
  validateControlAssignments(document);
  validateCollections(document);

  const raw = document as ProfileDocument & { behavior_defs?: unknown };
  const canonicalBehaviors = isPlainObject(document.behaviors) ? document.behaviors : null;
  const legacyBehaviors = isPlainObject(raw.behavior_defs) ? raw.behavior_defs : null;
  if (canonicalBehaviors && legacyBehaviors
    && stableSignature(canonicalBehaviors) !== stableSignature(legacyBehaviors)) {
    throw new Error("Profile 同时包含不同的 behaviors 与旧字段 behavior_defs，无法安全合并");
  }
  if (!canonicalBehaviors && legacyBehaviors) {
    document.behaviors = legacyBehaviors as unknown as ProfileDocument["behaviors"];
  }
  delete raw.behavior_defs;

  if (!document.behaviors || typeof document.behaviors !== "object" || Array.isArray(document.behaviors)) {
    document.behaviors = {};
  }
  if (!Array.isArray(document.interaction_rules)) {
    document.interaction_rules = [];
  }
  if (!document.macro_defs || typeof document.macro_defs !== "object" || Array.isArray(document.macro_defs)) {
    document.macro_defs = {};
  }
  const reportRate: Record<string, unknown> = isPlainObject(document.report_rate_policy)
    ? document.report_rate_policy
    : {};
  document.report_rate_policy = {
    ...reportRate,
    usb_report_rate_hz: Number.isInteger(reportRate.usb_report_rate_hz)
      ? reportRate.usb_report_rate_hz as number
      : 8000,
    ch585_wireless_report_rate_hz: Number.isInteger(reportRate.ch585_wireless_report_rate_hz)
      ? reportRate.ch585_wireless_report_rate_hz as number
      : 1000,
  };
  const inputGuard: Record<string, unknown> = isPlainObject(document.input_guard_policy)
    ? document.input_guard_policy
    : {};
  document.input_guard_policy = {
    ...inputGuard,
    win_key_lock_enabled: typeof inputGuard.win_key_lock_enabled === "boolean"
      ? inputGuard.win_key_lock_enabled
      : false,
  };

  return document;
}
