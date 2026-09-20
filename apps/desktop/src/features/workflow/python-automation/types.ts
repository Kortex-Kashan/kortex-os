/**
 * Types for the Python Automation surface (AI Studio functional
 * stabilization, Phase E). Mirrors the existing, already-governed backend
 * shapes this feature calls directly:
 * `kortex.engines.python_exec.models.PythonAction` (via
 * `kortex.python.action.list`) and `PythonExecutionResult` (via
 * `kortex.python.execute`). No new backend concept is introduced — this is
 * purely a frontend surface over capabilities that already exist and are
 * already governed by `CapabilityDispatcher`.
 */

export interface PythonAction {
  actionId: string;
  name: string;
  description: string;
  latestVersion: number;
  isActive: boolean;
}

export interface PythonExecutionResult {
  status: string;
  output: unknown;
  error: string | null;
  exitCode: number | null;
  durationMs: number;
}
