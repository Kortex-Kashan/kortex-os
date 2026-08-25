/**
 * Theme registry. Actual token values live in ../styles/tokens.css as CSS
 * variables (`:root` for light, `.dark` for dark) and are toggled by adding
 * or removing the `dark` class on the document root (ADR-0002 §10.4). This
 * module only names the themes available to the rest of the system.
 */

export type ThemeName = "light" | "dark";

export const themes: readonly ThemeName[] = ["light", "dark"];
