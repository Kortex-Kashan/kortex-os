import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { useUiStore } from "@/stores/uiStore";

import { useThemeSync } from "./useTheme";

afterEach(() => {
  useUiStore.setState({ theme: "light" });
  document.documentElement.classList.remove("dark");
});

describe("useThemeSync", () => {
  it("does not add the dark class to the document root when the theme is light", () => {
    renderHook(() => useThemeSync());

    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("adds the dark class to the document root when the theme is dark on mount", () => {
    act(() => {
      useUiStore.setState({ theme: "dark" });
    });

    renderHook(() => useThemeSync());

    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("adds the dark class when the theme changes from light to dark after mount", () => {
    renderHook(() => useThemeSync());
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    act(() => {
      useUiStore.setState({ theme: "dark" });
    });

    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("removes the dark class when the theme changes from dark back to light after mount", () => {
    act(() => {
      useUiStore.setState({ theme: "dark" });
    });
    renderHook(() => useThemeSync());
    expect(document.documentElement.classList.contains("dark")).toBe(true);

    act(() => {
      useUiStore.setState({ theme: "light" });
    });

    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });
});
