import { AiStudioIcon, ConnectorIcon, DashboardIcon, MarketplaceIcon, WorkflowIcon } from "./icons";
import { createPlaceholderApplication } from "./PlaceholderApplication";
import type { WorkspaceApplication } from "./workspaceTypes";

export const DEFAULT_APPLICATIONS: WorkspaceApplication[] = [
  {
    id: "dashboard",
    name: "Dashboard",
    description: "Cross-engine overview and system health at a glance.",
    icon: DashboardIcon,
    route: "/dashboard",
    component: createPlaceholderApplication(
      "Dashboard",
      "Cross-engine overview and system health at a glance.",
    ),
    permissions: ["kortex.dashboard.view"],
  },
  {
    id: "ai-studio",
    name: "AI Studio",
    description: "Build and supervise AI-orchestrated workflows.",
    icon: AiStudioIcon,
    route: "/ai-studio",
    component: createPlaceholderApplication(
      "AI Studio",
      "Build and supervise AI-orchestrated workflows.",
    ),
    permissions: ["kortex.ai.studio.view"],
  },
  {
    id: "workflow-engine",
    name: "Workflow Engine",
    description: "Design and monitor automated business workflows.",
    icon: WorkflowIcon,
    route: "/workflows",
    component: createPlaceholderApplication(
      "Workflow Engine",
      "Design and monitor automated business workflows.",
    ),
    permissions: ["kortex.workflow.view"],
  },
  {
    id: "connector-engine",
    name: "Connector Engine",
    description: "Manage integrations with external systems.",
    icon: ConnectorIcon,
    route: "/connectors",
    component: createPlaceholderApplication(
      "Connector Engine",
      "Manage integrations with external systems.",
    ),
    permissions: ["kortex.connector.view"],
  },
  {
    id: "marketplace",
    name: "Marketplace",
    description: "Discover and install KORTEX modules and templates.",
    icon: MarketplaceIcon,
    route: "/marketplace",
    component: createPlaceholderApplication(
      "Marketplace",
      "Discover and install KORTEX modules and templates.",
    ),
    permissions: ["kortex.marketplace.view"],
  },
];
