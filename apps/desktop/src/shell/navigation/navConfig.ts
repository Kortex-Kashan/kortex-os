export interface NavItem {
  id: string;
  label: string;
}

export interface NavGroup {
  id: string;
  label: string;
  items: NavItem[];
}

/**
 * Navigation placeholders only (M2.1 scope) — no route is wired to any of
 * these yet, so every item renders disabled. Later milestones wire real
 * routes/pages behind these labels rather than adding new ones here.
 *
 * "AI Studio" and "Marketplace" were removed from here in M2.3: those two
 * labels now have a real, live destination in AppSidebar's Applications
 * group (backed by the workspace's default applications), so keeping
 * identically-labeled disabled entries here would put two elements with
 * the same accessible name in the same sidebar — one functional, one a
 * permanent dead end.
 */
export const NAV_GROUPS: NavGroup[] = [
  {
    id: "core",
    label: "Core",
    items: [
      { id: "kernel", label: "Kernel" },
      { id: "system-engines", label: "System Engines" },
      { id: "event-monitor", label: "Event Monitor" },
      { id: "registry", label: "Registry" },
      { id: "configuration", label: "Configuration" },
    ],
  },
  {
    id: "modules",
    label: "Modules",
    items: [
      { id: "my-modules", label: "My Modules" },
      { id: "templates", label: "Templates" },
      { id: "connectors", label: "Connectors" },
    ],
  },
  {
    id: "automation",
    label: "Automation",
    items: [
      { id: "workflows", label: "Workflows" },
      { id: "recipes", label: "Recipes" },
      { id: "scheduler", label: "Scheduler" },
      { id: "approvals", label: "Approvals" },
    ],
  },
  {
    id: "intelligence",
    label: "Intelligence",
    items: [
      { id: "knowledge", label: "Knowledge" },
      { id: "analytics", label: "Analytics" },
      { id: "insights", label: "Insights" },
    ],
  },
  {
    id: "system",
    label: "System",
    items: [
      { id: "users-roles", label: "Users & Roles" },
      { id: "security", label: "Security" },
      { id: "audit-logs", label: "Audit Logs" },
      { id: "settings", label: "Settings" },
    ],
  },
];
