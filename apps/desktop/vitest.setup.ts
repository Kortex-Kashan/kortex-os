import "@testing-library/jest-dom/vitest";

// jsdom does not implement these APIs; Radix UI's Menu/Popper primitives
// (consumed from @kortex/design-system by Select/DropdownMenu/Tooltip/
// NavigationMenu/etc.) call them internally. Mirrors design-system's own
// vitest.setup.ts, which needs the same polyfills for the same reason.
if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false;
}
if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = () => {};
}
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}
if (typeof window.ResizeObserver === "undefined") {
  window.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
