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
 *
 * Visual transformation milestone: added `business` and `ai` groups so the
 * sidebar reads as a business-facing product (CRM/ERP/Analytics/Browser/
 * Voice, AI Agents/MCP) rather than only exposing engine-internal concepts
 * up top. `Analytics` moved out of `intelligence` into `business` — it must
 * have exactly one navigation identity, not two placeholders with the same
 * label — every other previously-existing item is unchanged and still
 * present, just consolidated under its original group.
 */
export const NAV_GROUPS: NavGroup[] = [
  {
    id: "business",
    label: "Business",
    items: [
      { id: "crm", label: "CRM" },
      { id: "erp", label: "ERP" },
      { id: "analytics", label: "Analytics" },
      { id: "browser", label: "Browser" },
      { id: "voice", label: "Voice" },
    ],
  },
  {
    id: "ai",
    label: "AI",
    items: [
      { id: "ai-agents", label: "AI Agents" },
      { id: "mcp", label: "MCP" },
    ],
  },
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
