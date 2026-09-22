import { cn } from "@kortex/design-system";

/**
 * The KORTEX mark: three colored bars (a vertical bar plus two skewed
 * bars), built from plain elements rather than an SVG/icon-font asset —
 * no binary logo file to track, easy to restyle from tokens alone since
 * every bar is a semantic color (`bg-primary`/`bg-chart-2`/`bg-chart-4`).
 */
export function KortexMark({ className }: { className?: string }) {
  return (
    <span className={cn("relative block size-8 shrink-0", className)} aria-hidden="true">
      <span className="absolute inset-y-0 left-0 w-2.5 rounded-sm bg-primary shadow-glow" />
      <span className="absolute left-2.5 top-0 h-3 w-6 origin-left -skew-x-[38deg] rounded-sm bg-chart-2" />
      <span className="absolute bottom-0 left-2.5 h-3 w-6 origin-left skew-x-[38deg] rounded-sm bg-chart-4" />
    </span>
  );
}

export function Brand({ compact = false, className }: { compact?: boolean; className?: string }) {
  return (
    <div className={cn("flex items-center gap-3", className)}>
      <KortexMark />
      <div className={cn(compact && "hidden xl:block")}>
        <div className="font-display text-[17px] font-semibold leading-none text-foreground">
          KORTEX <span className="text-primary">OS</span>
        </div>
        <div className="mt-1 text-[9px] font-medium uppercase tracking-[0.16em] text-cyan">
          AI Business Operating System
        </div>
      </div>
    </div>
  );
}
