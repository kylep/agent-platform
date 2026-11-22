export type UiFont = { sha256: string; family: string; weight: number; license: string; license_text: string; glyphs?: number; units_per_em?: number };
export type UiFonts = { version: number; fonts: UiFont[] };
export const BUNDLED_FONT_FAMILIES = ["Inter", "JetBrains Mono", "Noto Sans", "Nunito", "Fredoka"];
