/**
 * AK Ergo 77 physical geometry.
 *
 * Coordinates are preserved from the approved CAD-derived preview and use the
 * original 1399 x 613 design space. Key x/y values are centers; hardware
 * rectangles use top-left coordinates.
 */

export type KeyboardSide = "left" | "right";
export type KeyboardRow = 1 | 2 | 3 | 4 | 5 | 6;
export type ChassisSide = "whole";

declare const keyControlIdBrand: unique symbol;

/** Canonical Profile control id in the form key_###. */
export type KeyControlId = string & {
  readonly [keyControlIdBrand]: true;
};

export type HardwareControlId = "fiveway_000" | "enc_000";
export type ControlId = KeyControlId | HardwareControlId;

export type FiveWayEvent =
  | "up"
  | "down"
  | "left"
  | "right"
  | "press"
  | "cw_step"
  | "ccw_step";

export type EncoderEvent = "cw_step" | "ccw_step" | "press";
export type HardwareControlEvent = FiveWayEvent | EncoderEvent;

export interface Size {
  readonly width: number;
  readonly height: number;
}

export interface Rect {
  readonly x: number;
  readonly y: number;
  readonly w: number;
  readonly h: number;
}

export interface ChassisRect extends Rect {
  readonly side: ChassisSide;
}

export interface DisplayRect extends Rect {
  readonly nativeWidth: 800;
  readonly nativeHeight: 480;
  readonly aspectRatio: number;
}

export interface HardwareControlRect extends Rect {
  readonly id: HardwareControlId;
}

export interface KeyboardKey extends Rect {
  readonly id: KeyControlId;
  readonly label: string;
  readonly side: KeyboardSide;
  readonly row: KeyboardRow;
  readonly noOp?: boolean;
}

interface KeyboardKeyInput extends Omit<KeyboardKey, "id"> {
  readonly id: string;
}

export const KEYBOARD_BOUNDS: Readonly<Size> = Object.freeze({
  width: 1399,
  height: 613,
});

const FIVEWAY_CONTROL: Readonly<HardwareControlRect> = Object.freeze({
  id: "fiveway_000",
  x: 620.28,
  y: 237.39,
  w: 82,
  h: 82,
});

const ENCODER_CONTROL: Readonly<HardwareControlRect> = Object.freeze({
  id: "enc_000",
  x: 640.28,
  y: 355.86,
  w: 42,
  h: 42,
});

export const KEYBOARD_HARDWARE = Object.freeze({
  chassis: Object.freeze<readonly ChassisRect[]>([
    // The negative x offset is intentional: the plate extends to the left of
    // the KLE key-matrix origin.
    Object.freeze({ side: "whole", x: -8, y: 2, w: 1398, h: 616 }),
  ]),
  // The module is fixed to the enclosure. Keep its visible area at the native
  // 800 x 480 (5:3) aspect ratio while using the CAD/silkscreen anchor.
  screen: Object.freeze<DisplayRect>({
    x: 536.78,
    y: 24.31,
    w: 249,
    h: 149.4,
    nativeWidth: 800,
    nativeHeight: 480,
    aspectRatio: 5 / 3,
  }),
  fiveway: FIVEWAY_CONTROL,
  encoder: ENCODER_CONTROL,
});

const key = (definition: KeyboardKeyInput): Readonly<KeyboardKey> =>
  Object.freeze({
    ...definition,
    id: definition.id as KeyControlId,
  });

export const KEYBOARD_KEYS: readonly Readonly<KeyboardKey>[] = Object.freeze([
  // Left function row
  key({ id: "key_046", label: "Esc", x: 74, y: 53, w: 61, h: 64, side: "left", row: 1 }),
  key({ id: "key_045", label: "F1", x: 155, y: 53, w: 61, h: 64, side: "left", row: 1 }),
  key({ id: "key_044", label: "F2", x: 236, y: 53, w: 61, h: 64, side: "left", row: 1 }),
  key({ id: "key_043", label: "F3", x: 317, y: 53, w: 61, h: 64, side: "left", row: 1 }),
  key({ id: "key_042", label: "F4", x: 398, y: 53, w: 61, h: 64, side: "left", row: 1 }),
  key({ id: "key_041", label: "F5", x: 479, y: 53, w: 61, h: 64, side: "left", row: 1 }),

  // Left alphanumeric block
  key({ id: "key_053", label: "~", x: 51, y: 176, w: 65, h: 64, side: "left", row: 2 }),
  key({ id: "key_052", label: "1", x: 132, y: 184, w: 65, h: 64, side: "left", row: 2 }),
  key({ id: "key_051", label: "2", x: 214, y: 194, w: 66, h: 64, side: "left", row: 2 }),
  key({ id: "key_050", label: "3", x: 294, y: 202, w: 65, h: 64, side: "left", row: 2 }),
  key({ id: "key_049", label: "4", x: 375, y: 210, w: 66, h: 64, side: "left", row: 2 }),
  key({ id: "key_048", label: "5", x: 455, y: 219, w: 65, h: 64, side: "left", row: 2 }),
  key({ id: "key_047", label: "6", x: 536, y: 227, w: 66, h: 64, side: "left", row: 2 }),
  key({ id: "key_060", label: "Tab", x: 65, y: 260, w: 106, h: 64, side: "left", row: 3 }),
  key({ id: "key_059", label: "Q", x: 165, y: 270, w: 65, h: 64, side: "left", row: 3 }),
  key({ id: "key_058", label: "W", x: 245, y: 279, w: 65, h: 64, side: "left", row: 3 }),
  key({ id: "key_057", label: "E", x: 326, y: 287, w: 65, h: 64, side: "left", row: 3 }),
  key({ id: "key_056", label: "R", x: 406, y: 296, w: 65, h: 64, side: "left", row: 3 }),
  key({ id: "key_055", label: "T", x: 487, y: 304, w: 65, h: 64, side: "left", row: 3 }),
  key({ id: "key_054", label: "Y", x: 567, y: 312, w: 65, h: 64, side: "left", row: 3 }),
  key({ id: "key_066", label: "Caps", x: 106, y: 346, w: 125, h: 64, side: "left", row: 4 }),
  key({ id: "key_065", label: "A", x: 216, y: 357, w: 65, h: 64, side: "left", row: 4 }),
  key({ id: "key_064", label: "S", x: 296, y: 365, w: 65, h: 64, side: "left", row: 4 }),
  key({ id: "key_063", label: "D", x: 377, y: 374, w: 66, h: 64, side: "left", row: 4 }),
  key({ id: "key_062", label: "F", x: 457, y: 382, w: 65, h: 64, side: "left", row: 4 }),
  key({ id: "key_061", label: "G", x: 538, y: 391, w: 66, h: 64, side: "left", row: 4 }),
  key({ id: "key_072", label: "Shift", x: 119, y: 428, w: 126, h: 64, side: "left", row: 5 }),
  key({ id: "key_071", label: "Z", x: 228, y: 439, w: 65, h: 64, side: "left", row: 5 }),
  key({ id: "key_070", label: "X", x: 309, y: 448, w: 65, h: 64, side: "left", row: 5 }),
  key({ id: "key_069", label: "C", x: 389, y: 456, w: 65, h: 64, side: "left", row: 5 }),
  key({ id: "key_068", label: "V", x: 470, y: 465, w: 66, h: 64, side: "left", row: 5 }),
  key({ id: "key_067", label: "B", x: 551, y: 474, w: 65, h: 64, side: "left", row: 5 }),
  key({ id: "key_076", label: "Ctrl", x: 109, y: 509, w: 85, h: 64, side: "left", row: 6 }),
  key({ id: "key_075", label: "Win", x: 210, y: 519, w: 86, h: 64, side: "left", row: 6 }),
  key({ id: "key_074", label: "Alt", x: 311, y: 530, w: 85, h: 64, side: "left", row: 6 }),
  key({ id: "key_073", label: "Space", x: 450, y: 544, w: 166, h: 64, side: "left", row: 6 }),

  // Right function row
  key({ id: "key_006", label: "F6", x: 843, y: 53, w: 61, h: 64, side: "right", row: 1 }),
  key({ id: "key_005", label: "F7", x: 924, y: 53, w: 61, h: 64, side: "right", row: 1 }),
  key({ id: "key_004", label: "F8", x: 1005, y: 53, w: 61, h: 64, side: "right", row: 1 }),
  key({ id: "key_003", label: "F9", x: 1086, y: 53, w: 61, h: 64, side: "right", row: 1 }),
  key({ id: "key_002", label: "F10", x: 1167, y: 53, w: 61, h: 64, side: "right", row: 1 }),
  key({ id: "key_001", label: "F11", x: 1248, y: 53, w: 61, h: 64, side: "right", row: 1 }),
  key({ id: "key_000", label: "F12", x: 1329, y: 53, w: 61, h: 64, side: "right", row: 1 }),

  // Right alphanumeric block
  key({ id: "key_013", label: "7", x: 788, y: 227, w: 66, h: 64, side: "right", row: 2 }),
  key({ id: "key_012", label: "8", x: 868, y: 218, w: 65, h: 64, side: "right", row: 2 }),
  key({ id: "key_011", label: "9", x: 949, y: 210, w: 66, h: 64, side: "right", row: 2 }),
  key({ id: "key_010", label: "0", x: 1029, y: 201, w: 65, h: 64, side: "right", row: 2 }),
  key({ id: "key_009", label: "−", x: 1109, y: 193, w: 66, h: 64, side: "right", row: 2 }),
  key({ id: "key_008", label: "=", x: 1190, y: 184, w: 65, h: 64, side: "right", row: 2 }),
  key({ id: "key_007", label: "Backspace", x: 1302, y: 175, w: 126, h: 64, side: "right", row: 2 }),
  key({ id: "key_021", label: "Y", x: 756, y: 312, w: 65, h: 64, side: "right", row: 3 }),
  key({ id: "key_020", label: "U", x: 836, y: 303, w: 66, h: 64, side: "right", row: 3 }),
  key({ id: "key_019", label: "I", x: 916, y: 295, w: 65, h: 64, side: "right", row: 3 }),
  key({ id: "key_018", label: "O", x: 998, y: 287, w: 65, h: 64, side: "right", row: 3 }),
  key({ id: "key_017", label: "P", x: 1078, y: 278, w: 65, h: 64, side: "right", row: 3 }),
  key({ id: "key_016", label: "[", x: 1159, y: 269, w: 65, h: 64, side: "right", row: 3 }),
  key({ id: "key_015", label: "]", x: 1239, y: 261, w: 66, h: 64, side: "right", row: 3 }),
  key({ id: "key_014", label: "\\", x: 1330, y: 251, w: 85, h: 64, side: "right", row: 3 }),
  key({ id: "key_028", label: "H", x: 784, y: 391, w: 66, h: 64, side: "right", row: 4 }),
  key({ id: "key_027", label: "J", x: 864, y: 382, w: 65, h: 64, side: "right", row: 4 }),
  key({ id: "key_026", label: "K", x: 945, y: 374, w: 66, h: 64, side: "right", row: 4 }),
  key({ id: "key_025", label: "L", x: 1026, y: 365, w: 65, h: 64, side: "right", row: 4 }),
  key({ id: "key_024", label: ";", x: 1106, y: 356, w: 66, h: 64, side: "right", row: 4 }),
  key({ id: "key_023", label: "'", x: 1187, y: 348, w: 65, h: 64, side: "right", row: 4 }),
  key({ id: "key_022", label: "Enter", x: 1288, y: 338, w: 105, h: 64, side: "right", row: 4 }),
  key({ id: "key_035", label: "B", x: 773, y: 473, w: 65, h: 64, side: "right", row: 5 }),
  key({ id: "key_034", label: "N", x: 853, y: 465, w: 66, h: 64, side: "right", row: 5 }),
  key({ id: "key_033", label: "M", x: 934, y: 456, w: 65, h: 64, side: "right", row: 5 }),
  key({ id: "key_032", label: ",", x: 1014, y: 447, w: 65, h: 64, side: "right", row: 5 }),
  key({ id: "key_031", label: ".", x: 1095, y: 439, w: 65, h: 64, side: "right", row: 5 }),
  key({ id: "key_030", label: "/", x: 1176, y: 430, w: 65, h: 64, side: "right", row: 5 }),
  key({ id: "key_029", label: "Shift", x: 1277, y: 421, w: 105, h: 64, side: "right", row: 5 }),
  key({ id: "key_040", label: "Space", x: 871, y: 544, w: 166, h: 64, side: "right", row: 6 }),
  key({ id: "key_039", label: "Alt", x: 1013, y: 530, w: 86, h: 64, side: "right", row: 6 }),
  key({ id: "key_038", label: "Fn", x: 1104, y: 520, w: 65, h: 64, side: "right", row: 6, noOp: true }),
  key({ id: "key_037", label: "Win", x: 1184, y: 512, w: 65, h: 64, side: "right", row: 6 }),
  key({ id: "key_036", label: "Ctrl", x: 1275, y: 502, w: 85, h: 64, side: "right", row: 6 }),
]);

export const KEYBOARD_KEY_BY_ID: ReadonlyMap<KeyControlId, Readonly<KeyboardKey>> =
  new Map(KEYBOARD_KEYS.map((item) => [item.id, item]));

/**
 * Convert raw KLE ids ("65"), padded ids ("065"), and Profile ids
 * ("key_065") to the application's canonical Profile form.
 * Non-key control ids and event ids are preserved.
 */
export function normalizeControlId(value: unknown): string {
  const raw = String(value ?? "").trim();
  const match = raw.match(/^(?:key_)?(\d+)$/i);

  if (!match) {
    return raw;
  }

  return "key_" + match[1].padStart(3, "0");
}

/** Compatibility alias for code migrated from the preview. */
export const normalizeKeyId = normalizeControlId;

export function isKeyControlId(value: unknown): value is KeyControlId {
  return KEYBOARD_KEY_BY_ID.has(normalizeControlId(value) as KeyControlId);
}

/** Resolve a UI/KLE key id to the exact id used by Profile bindings. */
export function toProfileKeyId(value: unknown): KeyControlId | undefined {
  const normalized = normalizeControlId(value) as KeyControlId;
  return KEYBOARD_KEY_BY_ID.has(normalized) ? normalized : undefined;
}

/** Resolve any physical base control id used by the current Profile schema. */
export function toProfileControlId(value: unknown): ControlId | undefined {
  const normalized = normalizeControlId(value);

  if (KEYBOARD_KEY_BY_ID.has(normalized as KeyControlId)) {
    return normalized as KeyControlId;
  }

  if (normalized === "fiveway_000" || normalized === "enc_000") {
    return normalized;
  }

  return undefined;
}

/**
 * Build the Profile binding key for a physical control. Keys map directly;
 * the five-way control and encoder append their event suffix.
 */
export function toProfileBindingId(
  controlId: ControlId,
  event?: HardwareControlEvent,
): string {
  if (event === undefined) {
    return controlId;
  }

  return controlId + "." + event;
}

export function getKeyboardKey(value: unknown): Readonly<KeyboardKey> | undefined {
  const profileId = toProfileKeyId(value);
  return profileId ? KEYBOARD_KEY_BY_ID.get(profileId) : undefined;
}
