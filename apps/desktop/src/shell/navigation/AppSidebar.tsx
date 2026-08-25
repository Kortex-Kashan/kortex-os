import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarTrigger,
} from "@kortex/design-system";

import { useApplicationNavigation } from "@/navigation/navigationBridge";
import { useWorkspace } from "@/workspace/WorkspaceProvider";

import { NAV_GROUPS } from "./navConfig";

export function AppSidebar() {
  const { applications } = useWorkspace();
  const { state, navigateToApplication } = useApplicationNavigation();

  return (
    <Sidebar>
      <SidebarHeader>
        <div className="flex items-center justify-between px-2 py-1">
          <span className="text-body font-semibold">Navigation</span>
          <SidebarTrigger />
        </div>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Applications</SidebarGroupLabel>
          <SidebarMenu>
            {applications.map((application) => {
              const Icon = application.icon;
              const isActive = application.id === state.applicationId;
              return (
                <SidebarMenuItem key={application.id}>
                  <SidebarMenuButton
                    active={isActive}
                    aria-current={isActive ? "page" : undefined}
                    onClick={() => navigateToApplication({ applicationId: application.id })}
                  >
                    <Icon className="size-4" aria-hidden="true" />
                    {application.name}
                  </SidebarMenuButton>
                </SidebarMenuItem>
              );
            })}
          </SidebarMenu>
        </SidebarGroup>
        {NAV_GROUPS.map((group) => (
          <SidebarGroup key={group.id}>
            <SidebarGroupLabel>{group.label}</SidebarGroupLabel>
            <SidebarMenu>
              {group.items.map((item) => (
                <SidebarMenuItem key={item.id}>
                  <SidebarMenuButton disabled title="Coming soon">
                    {item.label}
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroup>
        ))}
      </SidebarContent>
      <SidebarFooter>
        <p className="px-2 text-caption text-muted-foreground">
          Additional sections are placeholders — wired to real areas in later milestones.
        </p>
      </SidebarFooter>
    </Sidebar>
  );
}
