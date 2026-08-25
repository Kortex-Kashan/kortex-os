/** @type {import('tailwindcss').Config} */
module.exports = {
  presets: [require("@kortex/design-system/tailwind.config")],
  content: [
    "./index.html",
    "./src/**/*.{ts,tsx}",
    "../../design-system/components/**/*.{ts,tsx}",
    "../../design-system/index.ts",
  ],
};
