import { describe, expect, it } from "vitest";

import type { WorkspaceApplication } from "@/workspace/workspaceTypes";

import {
  isKnownApplicationRoute,
  resolveApplicationIdForRoute,
  resolveRouteForApplicationId,
  toRouterChildPath,
  WORKSPACE_ROOT_ROUTE,
} from "./applicationRouter";

function makeApps(): WorkspaceApplication[] {
  return [
    {
      id: "dashboard",
      name: "Dashboard",
      description: "d",
      icon: () => null,
      route: "/dashboard",
      component: () => null,
      permissions: [],
    },
    {
      id: "ai-studio",
      name: "AI Studio",
      description: "a",
      icon: () => null,
      route: "/ai-studio",
      component: () => null,
      permissions: [],
    },
  ];
}

describe("resolveRouteForApplicationId", () => {
  it("maps a known application id to its route", () => {
    expect(resolveRouteForApplicationId(makeApps(), "dashboard")).toBe("/dashboard");
  });

  it("returns null for an unknown application id", () => {
    expect(resolveRouteForApplicationId(makeApps(), "does-not-exist")).toBeNull();
  });
});

describe("resolveApplicationIdForRoute", () => {
  it("resolves a known route to its application id", () => {
    expect(resolveApplicationIdForRoute(makeApps(), "/ai-studio")).toBe("ai-studio");
  });

  it("returns null for an unknown route", () => {
    expect(resolveApplicationIdForRoute(makeApps(), "/does-not-exist")).toBeNull();
  });
});

describe("isKnownApplicationRoute", () => {
  it("treats the workspace root as a known route", () => {
    expect(isKnownApplicationRoute(makeApps(), WORKSPACE_ROOT_ROUTE)).toBe(true);
  });

  it("treats a registered application route as known", () => {
    expect(isKnownApplicationRoute(makeApps(), "/dashboard")).toBe(true);
  });

  it("treats an unrecognized path as unknown", () => {
    expect(isKnownApplicationRoute(makeApps(), "/does-not-exist")).toBe(false);
  });

  it("treats an empty application list as having no known routes beyond root", () => {
    expect(isKnownApplicationRoute([], "/dashboard")).toBe(false);
    expect(isKnownApplicationRoute([], WORKSPACE_ROOT_ROUTE)).toBe(true);
  });
});

describe("toRouterChildPath", () => {
  it("strips a single leading slash", () => {
    expect(toRouterChildPath("/dashboard")).toBe("dashboard");
  });

  it("strips multiple leading slashes", () => {
    expect(toRouterChildPath("//dashboard")).toBe("dashboard");
  });

  it("leaves a path with no leading slash unchanged", () => {
    expect(toRouterChildPath("dashboard")).toBe("dashboard");
  });
});
