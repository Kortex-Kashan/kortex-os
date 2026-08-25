import type { WorkspaceApplication } from "./workspaceTypes";

/**
 * A plain, instantiable registry — deliberately not a module-level
 * singleton (`export const registry = new WorkspaceRegistry()`), since ES
 * module caching would make that a de facto global shared by every
 * importer. Callers (WorkspaceProvider, tests) construct their own
 * instance, which is what keeps this testable in isolation.
 */
export class WorkspaceRegistry {
  private readonly applications = new Map<string, WorkspaceApplication>();

  register(application: WorkspaceApplication): void {
    if (this.applications.has(application.id)) {
      throw new Error(`Workspace application "${application.id}" is already registered.`);
    }
    this.applications.set(application.id, application);
  }

  unregister(id: string): void {
    this.applications.delete(id);
  }

  get(id: string): WorkspaceApplication | undefined {
    return this.applications.get(id);
  }

  has(id: string): boolean {
    return this.applications.has(id);
  }

  list(): WorkspaceApplication[] {
    return Array.from(this.applications.values());
  }
}
