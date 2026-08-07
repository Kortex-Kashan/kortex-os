# KORTEX OS — Secure Development Guide

Status: Approved Security Guide  
Authority: Chief Architect (KASHAN)  
Reference Architecture: Architecture Version 1.0.0 (`docs/architecture/ARCHITECTURE_VERSION_1.0.md`)  

---

## 1. Security Philosophy

In accordance with Article 14 of the KORTEX OS Engineering Constitution, **security is built into the architecture**.

All capabilities require authorization, input parameter sanitization, multi-tenant isolation, and secret management.

---

## 2. Secure Coding Mandates

1. **Authorization Middleware**: Every capability invocation MUST pass Kernel authorization checks (`SecurityEngine`).
2. **Multi-Tenant Isolation**: Every database query MUST filter by `tenant_id`. Cross-tenant data leakage is a critical vulnerability.
3. **Secret Storage**: Credentials, tokens, and keys MUST be stored in Security Engine (`SecretStore`) using encrypted secret handles.
4. **Input Validation**: All capability input parameters MUST be validated against Pydantic v2 schemas (`UniversalValidationReport`).
5. **Path Traversal Protection**: File operations MUST use `IFileStore` and `PathSandboxValidator`.
6. **No Arbitrary Code Execution**: Declarative assets (Recipes, Templates, Profiles) MUST NEVER execute arbitrary system commands or Python code.
