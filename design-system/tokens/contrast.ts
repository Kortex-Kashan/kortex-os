/**
 * WCAG 2.1 relative-luminance and contrast-ratio calculations, applied to
 * KORTEX's HSL design tokens (../styles/tokens.css). Implements the WCAG 2.1
 * §1.4.3 formulas directly rather than adding a third-party contrast-checking
 * dependency (consistent with the "avoid unnecessary dependencies" policy
 * already applied throughout design-system, ADR-0002 §21.1).
 */

export interface HslColor {
  h: number; // degrees, 0-360
  s: number; // fraction, 0-1
  l: number; // fraction, 0-1
}

export interface RgbColor {
  r: number; // 0-255
  g: number; // 0-255
  b: number; // 0-255
}

/** WCAG 2.1 AA minimum contrast ratio for normal-weight text (spec §10.6). */
export const WCAG_AA_NORMAL_TEXT_MIN_RATIO = 4.5;

/** WCAG 2.1 AA minimum contrast ratio for large-scale (>=18pt, or >=14pt bold) text and UI components. */
export const WCAG_AA_LARGE_TEXT_MIN_RATIO = 3.0;

/** Parses a token value in the `H S% L%` format used by tokens.css (e.g. "221 83% 53%"). */
export function parseHsl(value: string): HslColor {
  const match = value.trim().match(/^(-?[\d.]+)\s+([\d.]+)%\s+([\d.]+)%$/);
  if (!match) {
    throw new Error(`Not a valid "H S% L%" token value: "${value}"`);
  }
  const [, h, s, l] = match;
  return { h: parseFloat(h), s: parseFloat(s) / 100, l: parseFloat(l) / 100 };
}

/** Standard HSL-to-sRGB conversion (0-255 per channel). */
export function hslToRgb({ h, s, l }: HslColor): RgbColor {
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l - c / 2;
  let r = 0;
  let g = 0;
  let b = 0;
  if (h < 60) [r, g, b] = [c, x, 0];
  else if (h < 120) [r, g, b] = [x, c, 0];
  else if (h < 180) [r, g, b] = [0, c, x];
  else if (h < 240) [r, g, b] = [0, x, c];
  else if (h < 300) [r, g, b] = [x, 0, c];
  else [r, g, b] = [c, 0, x];
  return { r: (r + m) * 255, g: (g + m) * 255, b: (b + m) * 255 };
}

function srgbChannelToLinear(channel8Bit: number): number {
  const c = channel8Bit / 255;
  return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

/** WCAG 2.1 §1.4.3 relative luminance of an sRGB color. */
export function relativeLuminance({ r, g, b }: RgbColor): number {
  return (
    0.2126 * srgbChannelToLinear(r) +
    0.7152 * srgbChannelToLinear(g) +
    0.0722 * srgbChannelToLinear(b)
  );
}

/**
 * WCAG 2.1 §1.4.3 contrast ratio between two token values in "H S% L%"
 * format. Symmetric in its two arguments — order does not matter.
 */
export function contrastRatio(tokenValueA: string, tokenValueB: string): number {
  const luminanceA = relativeLuminance(hslToRgb(parseHsl(tokenValueA)));
  const luminanceB = relativeLuminance(hslToRgb(parseHsl(tokenValueB)));
  const [lighter, darker] =
    luminanceA >= luminanceB ? [luminanceA, luminanceB] : [luminanceB, luminanceA];
  return (lighter + 0.05) / (darker + 0.05);
}

/**
 * Extracts `--name: value;` custom properties declared inside a single CSS
 * rule block (e.g. `:root { ... }` or `.dark { ... }`) from a stylesheet's
 * source text. Reads the actual token file rather than a hand-copied value
 * table, so this check always tracks the current design-system contract.
 */
export function extractCssCustomProperties(
  cssSource: string,
  selector: string,
): Record<string, string> {
  const escapedSelector = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const blockMatch = cssSource.match(new RegExp(`${escapedSelector}\\s*\\{([^}]*)\\}`));
  if (!blockMatch) {
    throw new Error(`No "${selector}" rule block found in the given CSS source.`);
  }
  const properties: Record<string, string> = {};
  const declarationPattern = /--([a-z0-9-]+)\s*:\s*([^;]+);/gi;
  let declaration: RegExpExecArray | null;
  while ((declaration = declarationPattern.exec(blockMatch[1])) !== null) {
    properties[declaration[1]] = declaration[2].trim();
  }
  return properties;
}
