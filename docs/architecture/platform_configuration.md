# KORTEX OS — Platform Configuration Architecture Specification

Status: Approved Architecture
Version: 1.0.0
Authority: KORTEX OS Engineering Constitution & Platform Architecture
Target Release: KORTEX OS Platform Architecture
Target File: `docs/architecture/platform_configuration.md`

Depends On:
- KORTEX OS AI Engineering Constitution (`AGENTS.md` & `docs/architecture/engineering_constitution.md`)
- Platform Service Contracts (`docs/architecture/platform_service_contracts.md`)
- Security Engine Specification (`docs/architecture/security_engine_implementation_spec.md`)

---

## 1. Configuration Engine (`kortex.engines.configuration`)

The Configuration Engine provides centralized, hierarchical, validated configuration management across KORTEX OS. In accordance with the Engineering Constitution, configuration parameters (paths, database URLs, rate limits, feature flags) MUST NEVER be hardcoded inside engine or module source code.

---

## 2. Configuration Sources & Hierarchical Priority

Configuration settings are resolved hierarchically. Higher-priority sources override lower-priority sources:

```
┌─────────────────────────────────────────────────────────┐  Priority: HIGHEST
│ 1. Tenant & Organization Overrides (DB / IDataStore)   │
├─────────────────────────────────────────────────────────┤
│ 2. Environment Variables (.env / System ENV)            │
├─────────────────────────────────────────────────────────┤
│ 3. Module & Engine Config Files (config.yaml)           │
├─────────────────────────────────────────────────────────┤
│ 4. System Default Declarative Schemas (Pydantic Defaults)│
└─────────────────────────────────────────────────────────┘  Priority: LOWEST
```

---

## 3. Environment Variables

Environment variables use prefix `KORTEX_` (e.g. `KORTEX_STORAGE_DATABASE_URL`, `KORTEX_LOG_LEVEL`). Environment variables override configuration files.

---

## 4. Tenant & Organization Overrides

Multi-tenant environments support organization-specific configuration overrides stored in `IDataStore` and cached in `ICacheStore` (e.g. tenant-specific branding, document output buckets, rate limits).

---

## 5. Module & Engine Overrides

Business modules declare configuration schemas (`schema.yaml`). Default module settings can be overridden by system administrators via Configuration Engine APIs.

---

## 6. Secret Management Integration

Sensitve credentials (passwords, API keys, private keys) are NEVER stored in plain text configuration files. Configuration schemas store **secret handles** (e.g. `secret:kortex/connectors/smtp_pass`) which are resolved at runtime via Security Engine (`SecretStore`).

---

## 7. Encryption at Rest

Sensitive configuration overrides stored in `IDataStore` are encrypted using authenticated symmetric encryption (AES-256-GCM).

---

## 8. Schema Validation

All configuration settings are validated against Pydantic v2 schemas during startup or update. Invalid parameters raise `ConfigurationValidationError` and abort initialization.

---

## 9. Hot Reload Architecture

The Configuration Engine supports dynamic hot-reloading for non-critical operational settings (feature flags, rate limits, log levels) without requiring Kernel restarts.

---

## 10. Acceptance Criteria

- ✓ **Zero Hardcoding**: 100% of environment, storage, and operational settings resolved via Configuration Engine.
- ✓ **Hierarchical Priority**: Tenant overrides > ENV > File > Defaults strictly enforced.
- ✓ **Secret Handles**: Plaintext passwords prohibited in config files; handles resolved via Security Engine.
- ✓ **Schema Validated**: Invalid configuration parameters caught during startup validation.
