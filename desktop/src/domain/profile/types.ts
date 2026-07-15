export interface ProfileIdentity {
  profile_id: string;
  name: string;
  category: string;
  schema_version: number;
  revision: number;
}

export interface BindingScope {
  priority: number;
  default_active: boolean;
  unbound: string;
  bindings: Record<string, string>;
}

export interface ReportRatePolicy {
  usb_report_rate_hz: number;
  ch585_wireless_report_rate_hz: number;
  [key: string]: unknown;
}

export interface InputGuardPolicy {
  win_key_lock_enabled: boolean;
  [key: string]: unknown;
}

export type AkeyTriggerMode = "normal" | "rapid_trigger" | "disabled";

export interface AkeyTriggerParams {
  defaults?: boolean;
  press_threshold_norm_i16?: number;
  release_threshold_norm_i16?: number;
  reset_threshold_norm_i16?: number;
  press_delta_norm_i16?: number;
  release_delta_norm_i16?: number;
  deadzone_norm_i16?: number;
  retrigger_before_reset?: boolean;
  [key: string]: unknown;
}

export interface AkeyControlAssignment {
  controls: string[];
  type: "akey";
  mode: AkeyTriggerMode;
  params: AkeyTriggerParams;
  [key: string]: unknown;
}

export interface ProfileDefaults {
  triggers?: {
    akey?: {
      common?: AkeyTriggerParams;
      normal?: AkeyTriggerParams;
      rapid_trigger?: AkeyTriggerParams;
      [key: string]: unknown;
    };
    [key: string]: unknown;
  };
  interactions?: {
    socd_resolution?: SocdResolution;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export type MacroUsageOperation =
  | "key_down"
  | "key_up"
  | "consumer_down"
  | "consumer_up";

export type MacroStep =
  | { op: MacroUsageOperation; usage: string }
  | { op: "pointer_step"; dx: number; dy: number; wheel: number }
  | { op: "delay_ticks"; ticks: number }
  | { op: "wait_release"; source: string }
  | { op: "stop" };

export interface MacroDefinition {
  steps: MacroStep[];
  repeat: "none";
  cancel_on_release: boolean;
}

export interface ProfileBehavior {
  kind: string;
  [key: string]: unknown;
}

export interface DksStage {
  range_um: {
    min_um: number;
    max_um: number;
  };
  enter: string;
}

export interface DksBehavior extends ProfileBehavior {
  kind: "dks";
  source: "travel";
  stages: DksStage[];
}

export type SocdResolution = "neutral" | "last_input" | "absolute_priority";

export interface ProfileInteractionRule {
  rule_id: string;
  kind: string;
  [key: string]: unknown;
}

export interface SocdInteractionRule extends ProfileInteractionRule {
  kind: "socd";
  members: [string, string];
  resolution: SocdResolution;
}

export interface ProfileDocument {
  identity: ProfileIdentity;
  compatibility: {
    keyboard_model_id: string;
    required_control_ids: string[];
    optional_modules: string[];
    [key: string]: unknown;
  };
  defaults: ProfileDefaults;
  control_assignments: Array<AkeyControlAssignment | Record<string, unknown>>;
  behaviors: Record<string, ProfileBehavior>;
  binding_scopes: Record<string, BindingScope>;
  interaction_rules: ProfileInteractionRule[];
  macro_defs: Record<string, MacroDefinition>;
  report_rate_policy: ReportRatePolicy;
  input_guard_policy: InputGuardPolicy;
  [key: string]: unknown;
}

export interface BridgeErrorPayload {
  code: string;
  message: string;
  recoverable?: boolean;
  details?: unknown;
}

export interface BridgeResponse<T = unknown> {
  id: string;
  ok: boolean;
  result?: T;
  error?: BridgeErrorPayload;
}
