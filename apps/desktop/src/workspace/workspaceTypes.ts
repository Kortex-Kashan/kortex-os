import type { ComponentType, SVGProps } from "react";

/**
 * A KORTEX application that can be hosted inside the workspace runtime.
 * `permissions` names capabilities in the `kortex.<domain>.<resource>.
 * <action>` format already ratified for the backend (platform_service_
 * contracts.md) — declared here for future enforcement once the Security
 * Engine is reachable via the IPC bridge (M3); nothing in M2.2 checks them
 * yet, this milestone only hosts and renders applications.
 */
export interface WorkspaceApplication {
  id: string;
  name: string;
  description: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  route: string;
  component: ComponentType;
  permissions: string[];
}
