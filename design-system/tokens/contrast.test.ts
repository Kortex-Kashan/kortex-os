import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  contrastRatio,
  extractCssCustomProperties,
  WCAG_AA_NORMAL_TEXT_MIN_RATIO,
} from "./contrast";

describe("contrastRatio()", () => {
  it("returns 21:1 for pure white on pure black (reference maximum)", () => {
    expect(contrastRatio("0 0% 100%", "0 0% 0%")).toBeCloseTo(21, 1);
  });

  it("returns 1:1 for identical colors (reference minimum)", () => {
    expect(contrastRatio("221 83% 53%", "221 83% 53%")).toBeCloseTo(1, 5);
  });

  it("is symmetric in its two arguments", () => {
    const a = "222 47% 11%";
    const b = "210 40% 98%";
    expect(contrastRatio(a, b)).toBeCloseTo(contrastRatio(b, a), 10);
  });
});

/**
 * M4 completion criterion (spec §17, ADR-0002 §4.4): "dark mode verified
 * visually and via automated contrast checks against the token palette."
 * This reads the actual ../styles/tokens.css contract (not a hand-copied
 * value table) and checks every semantic background/foreground pair — the
 * "-foreground" naming convention (documented in styles/tokens.css and
 * components/README.md) is itself the declaration that a token is meant to
 * render text over its paired background.
 */
describe("Design token contrast (WCAG 2.1 AA, spec §10.6)", () => {
  const tokensCssPath = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../styles/tokens.css",
  );
  const tokensCssSource = readFileSync(tokensCssPath, "utf-8");

  const themes = {
    light: extractCssCustomProperties(tokensCssSource, ":root"),
    dark: extractCssCustomProperties(tokensCssSource, ".dark"),
  } as const;

  const SEMANTIC_TEXT_PAIRS: ReadonlyArray<{ background: string; foreground: string }> = [
    { background: "background", foreground: "foreground" },
    { background: "card", foreground: "card-foreground" },
    { background: "primary", foreground: "primary-foreground" },
    { background: "secondary", foreground: "secondary-foreground" },
    { background: "muted", foreground: "muted-foreground" },
    { background: "accent", foreground: "accent-foreground" },
    { background: "destructive", foreground: "destructive-foreground" },
  ];

  for (const [themeName, tokens] of Object.entries(themes)) {
    describe(`${themeName} theme (.${themeName === "dark" ? "dark" : "root"})`, () => {
      for (const { background, foreground } of SEMANTIC_TEXT_PAIRS) {
        it(`"--${foreground}" on "--${background}" meets WCAG AA normal text (>= ${WCAG_AA_NORMAL_TEXT_MIN_RATIO}:1)`, () => {
          const backgroundValue = tokens[background];
          const foregroundValue = tokens[foreground];

          expect(
            backgroundValue,
            `--${background} not found in the ${themeName} theme block of tokens.css`,
          ).toBeDefined();
          expect(
            foregroundValue,
            `--${foreground} not found in the ${themeName} theme block of tokens.css`,
          ).toBeDefined();

          const ratio = contrastRatio(backgroundValue, foregroundValue);

          expect(
            ratio,
            `Token pair --${foreground} (${foregroundValue}) on --${background} (${backgroundValue}) ` +
              `in the ${themeName} theme has a contrast ratio of ${ratio.toFixed(2)}:1, ` +
              `below the WCAG AA minimum of ${WCAG_AA_NORMAL_TEXT_MIN_RATIO}:1 required for normal text.`,
          ).toBeGreaterThanOrEqual(WCAG_AA_NORMAL_TEXT_MIN_RATIO);
        });
      }
    });
  }
});
