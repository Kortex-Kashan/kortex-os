# KORTEX OS — AI Orchestration Engine Milestone 6: Tool Invocation Engine Specification

**Status: APPROVED ARCHITECTURE — authoritative contract for M6 implementation.**

Baseline: M5 commit `5b01b3e` (Context Composition & Knowledge Retrieval). Every contract cited was verified against committed source.

---

## 1. Scope & Ownership Boundaries

M6 delivers the **Tool Invocation Engine**: validating LLM-requested tool calls, enforcing argument/output safety boundaries, delegating execution through an abstract execution port, normalizing results into safe, sanitized string representations, and rendering them under the `[[tool]]` marker for context assembly.

### 1.1 Strict Responsibility & Ownership Matrix

| Subsystem / Concern | Authority / Owner | Description & Boundary |
|---|---|---|
| **Authoritative Platform Capabilities** | `RegistryEngine` / Kernel | Single source of truth for all capabilities in KORTEX OS. |
| **Production Authorization & RBAC** | `SecurityEngine` / Kernel | Kernel `CapabilityDispatcher` evaluates session tokens and RBAC/ABAC rules. |
| **AI Tool Definitions (`ToolDefinition`)** | **M6 (`ToolRegistry`)** | Local catalog of AI-presentable schemas exposed in prompts. Not capability authority. |
| **Capability Synchronization** | M8 Integration | Synchronizes AI `ToolDefinition` schemas from `RegistryEngine` descriptors. |
| **Tool Execution Authority** | Kernel Dispatcher | AI Engine never executes capabilities directly. Dispatches via `IToolExecutionPort`. |
| **Mock Authorizer (`ToolAuthorizer`)** | **M6 Test Fixture** | Test/fake hook only. Never used as a substitute for production Security Engine. |
| **Tool Call Validation & Bounds** | **M6 (`AIToolInvoker`)** | Validates tool names, schemas, and enforces byte/char limits before dispatch. |
| **Result Sanitization & Formatting** | **M6 (`ToolResult`)** | Serializes to JSON, neutralizes `[[` sentinels, enforces output size limits. |
| **Agent Loops & Re-prompting** | M7 (`AgentOrchestrator`) | M6 is single-invocation only. M7 manages multi-step loops and reasoning. |

---

## 2. Hardened Safety & Execution Boundaries

### 2.1 Argument & Output Size Guardrails
To prevent Denial of Service (DoS), memory exhaustion, and context window blowout, M6 enforces hard limits:
- `MAX_TOOL_ARGUMENTS_BYTES = 65_536` (64 KB): Rejects malformed or bloated JSON argument payloads before validation.
- `MAX_TOOL_OUTPUT_CHARS = 50_000` (~10,000 tokens): Truncates oversized outputs deterministically with a `[TRUNCATED: output exceeded 50000 chars]` suffix.
- `MAX_BATCH_SIZE = 10`: Limits batch tool invocations per step.
- `TIMEOUT_RANGE = [0.1s, 300.0s]` (Default `30.0s`).

### 2.2 Strict Prohibition of Nested Tool Execution
- M6 executes a **single invocation step** (or batch of parallel calls) and returns `ToolResult`.
- M6 contains **zero recursion**, zero internal re-prompting, and zero nested capability calls.
- Multi-step loops, retry decisions, and tool output interpretation are owned exclusively by Milestone 7 (`AgentOrchestrator`).

### 2.3 Hardened Serialization & Sentinel Neutralization
- Raw Python execution objects (database records, binary buffers, internal classes) must NEVER enter context directly.
- All outputs are serialized to deterministic JSON or clean text strings.
- All outputs are processed through `sanitize_context_content()` (`[[` neutralized to `[ [`).
- Rendered exclusively under `[[tool]]\ncall_id: <id>\ntool: <name>\nstatus: <STATUS>\npayload: <sanitized_json>`.

---

## 3. Core Contracts & Domain Models

### 3.1 `ToolExecutionStatus`
```python
class ToolExecutionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    DENIED = "DENIED"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    NOT_FOUND = "NOT_FOUND"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    TIMEOUT = "TIMEOUT"
```

### 3.2 `ToolDefinition` (Frozen Model)
```python
class ToolDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, pattern=r"^[a-zA-Z0-9_-]+$")
    description: str = Field(min_length=1)
    parameters_schema: dict[str, Any] = Field(default_factory=dict)
    canonical_capability: str = Field(min_length=1)
    is_mutation: bool = False
    timeout_seconds: float = Field(default=30.0, ge=0.1, le=300.0)
```

### 3.3 `ToolCall` (Frozen Model)
```python
class ToolCall(BaseModel):
    model_config = ConfigDict(frozen=True)

    call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
```

### 3.4 `ToolResult` (Frozen Model)
```python
class ToolResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    call_id: str
    tool_name: str
    status: ToolExecutionStatus
    output: Any = None
    error_message: str | None = None
    execution_time_ms: float = 0.0

    def to_context_entry(self) -> str:
        """Format and sanitize output into a [[tool]] context document with size bounding."""
        ...
```

### 3.5 `IToolExecutionPort` Protocol
```python
@runtime_checkable
class IToolExecutionPort(Protocol):
    async def execute_tool(
        self,
        tenant_id: str,
        capability_name: str,
        arguments: dict[str, Any],
        authorizer: ToolAuthorizer | None = None,
    ) -> Any:
        """Execute a tool capability across the boundary."""
        ...
```

---

## 4. Exception Hierarchy (`exceptions.py`)

```
KortexError
 └── AIOrchestrationError
      └── ToolInvocationError (Base)
           ├── ToolValidationError (Payload > 64KB or schema mismatch)
           ├── ToolNotFoundError (Unregistered tool name)
           ├── ToolAuthorizationError (Mock authorizer denied execution)
           ├── ToolExecutionError (Port threw unhandled exception)
           └── ToolTimeoutError (Execution exceeded timeout limit)
```

---

## 5. Dependency Invariants

The AST import quarantine test (`test_ai_package_imports_no_forbidden_dependency`) continues to enforce zero imports of:
- `kortex.core.kernel`
- `kortex.core.container`
- `kortex.engines.security`
- `kortex.engines.knowledge`
