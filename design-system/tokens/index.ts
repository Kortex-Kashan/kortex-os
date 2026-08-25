/**
 * Non-color design tokens. Color, spacing, and radius tokens are defined as
 * CSS variables in ../styles/tokens.css and consumed directly by
 * ../tailwind.config.js — they are not duplicated here to avoid drift
 * (ADR-0002 §10.2: "Tailwind config *is* the token layer").
 */

export const motionTokens = {
  duration: {
    fast: 0.15,
    base: 0.2,
    slow: 0.32,
  },
  easing: {
    standard: [0.4, 0, 0.2, 1] as const,
  },
} as const;
