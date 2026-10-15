/** The single color source for sprites, canvas rendering and web app styles.
 * A dot is a transparent pixel, never a background color.
 */
export const PALETTE = Object.freeze({
  ".": null,
  "K": "#141b2d",
  "S": "#35435c",
  "W": "#f4edd8",
  "N": "#ca936b",
  "B": "#729fe3",
  "V": "#a078d1",
  "G": "#70b892",
  "Y": "#e5bd65",
  "R": "#cc6b79",
  "C": "#6bbbc5",
  "O": "#df9560",
  "P": "#d58fce",
  "L": "#94bd6b",
  "H": "#9da8d6",
  "T": "#78c5b4",
  "E": "#e0a7ad"
});

/** Palette symbols for the world's fourteen area tiles. */
export const AREA_COLORS = Object.freeze({
  "A1": "G",
  "A2": "B",
  "A3": "V",
  "A4": "R",
  "A5": "O",
  "A6": "Y",
  "A7": "C",
  "A8": "N",
  "A9": "W",
  "A10": "P",
  "A11": "L",
  "A12": "H",
  "A13": "T",
  "A14": "E"
});

export const UI_COLORS = Object.freeze({
  background: PALETTE.K,
  surface: PALETTE.S,
  text: PALETTE.W,
  muted: PALETTE.B,
  accent: PALETTE.Y,
  border: PALETTE.V,
});
