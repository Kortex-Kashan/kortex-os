import { AiStudioApp } from "@/features/ai-studio/components/AiStudioApp";
import { ConnectorsApp } from "@/features/connectors/components/ConnectorsApp";
import { Dashboard } from "@/features/dashboard/components/Dashboard";
import { DocumentKnowledgeApp } from "@/features/document-knowledge/components/DocumentKnowledgeApp";
import { MarketplaceApp } from "@/features/marketplace/components/MarketplaceApp";
import { WorkflowApp } from "@/features/workflow/components/WorkflowApp";
import {
  AiStudioIcon,
  ConnectorIcon,
  DashboardIcon,
  DocumentKnowledgeIcon,
  MarketplaceIcon,
  WorkflowIcon,
} from "./icons";
import type { WorkspaceApplication } from "./workspaceTypes";

export const DEFAULT_APPLICATIONS: WorkspaceApplication[] = [
  {
    id: "dashboard",
    name: "Dashboard",
    description: "Cross-engine overview and system health at a glance.",
    icon: DashboardIcon,
    route: "/dashboard",
    component: Dashboard,
    permissions: ["kortex.dashboard.view"],
  },
  {
    id: "ai-studio",
    name: "AI Studio",
    description: "Browse the AI provider and model registry.",
    icon: AiStudioIcon,
    route: "/ai-studio",
    component: AiStudioApp,
    permissions: ["kortex.ai.studio.view"],
  },
  {
    id: "workflow-engine",
    name: "Workflow Engine",
    description: "Design and monitor automated business workflows.",
    icon: WorkflowIcon,
    route: "/workflows",
    component: WorkflowApp,
    permissions: ["kortex.workflow.view"],
  },
  {
    id: "connector-engine",
    name: "Connector Engine",
    description: "Manage integrations with external systems.",
    icon: ConnectorIcon,
    route: "/connectors",
    component: ConnectorsApp,
    permissions: ["kortex.connector.view"],
  },
  {
    id: "marketplace",
    name: "Marketplace",
    description: "Discover KORTEX modules and templates.",
    icon: MarketplaceIcon,
    route: "/marketplace",
    component: MarketplaceApp,
    permissions: ["kortex.marketplace.view"],
  },
  {
    id: "document-knowledge",
    name: "Document & Knowledge",
    description: "Browse the document adapter/template and knowledge graph registries.",
    icon: DocumentKnowledgeIcon,
    route: "/document-knowledge",
    component: DocumentKnowledgeApp,
    permissions: ["kortex.document.view", "kortex.knowledge.view"],
  },
];
