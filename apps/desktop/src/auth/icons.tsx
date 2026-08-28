import type { SVGProps } from "react";

/**
 * Small inline glyphs for the login screen only — mirrors `shell/icons.tsx`'s
 * established convention (one-off icons kept component-local rather than
 * adding an icon library dependency), not a design-system primitive.
 */

function baseProps(props: SVGProps<SVGSVGElement>): SVGProps<SVGSVGElement> {
  return {
    xmlns: "http://www.w3.org/2000/svg",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": true,
    ...props,
  };
}

export function EyeIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...baseProps(props)}>
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

export function EyeOffIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...baseProps(props)}>
      <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c6.5 0 10 7 10 7a13.3 13.3 0 0 1-3.14 4.24" />
      <path d="M6.1 6.1C3.9 7.6 2 10.5 2 11.5c0 1 3.5 7.5 10 7.5 1.5 0 2.83-.34 4-.9" />
      <path d="M9.5 10.5a3 3 0 0 0 4 4" />
      <path d="M2 2l20 20" />
    </svg>
  );
}
