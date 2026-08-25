import { Separator } from "@kortex/design-system";
import { version } from "../../package.json";

import { LockIcon } from "./icons";

export function StatusBar() {
  return (
    <footer className="flex h-8 shrink-0 items-center justify-between border-t border-border bg-card px-4 text-caption text-muted-foreground">
      <div className="flex items-center gap-3">
        <span>KORTEX OS v{version}</span>
        <Separator orientation="vertical" className="h-3" />
        <span className="flex items-center gap-1.5">
          <span className="size-1.5 rounded-full bg-primary" aria-hidden="true" />
          Local-first
        </span>
      </div>
      <div className="flex items-center gap-1.5">
        <LockIcon className="size-3" />
        <span>Local session</span>
      </div>
    </footer>
  );
}
