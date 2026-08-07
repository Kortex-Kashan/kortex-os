# KORTEX OS — Marketplace Architecture Specification

Status: Approved Architecture
Version: 1.0.0
Authority: KORTEX OS Engineering Constitution & Platform Architecture
Target Release: KORTEX OS Platform Architecture
Target File: `docs/architecture/marketplace_architecture.md`

Depends On:
- KORTEX OS AI Engineering Constitution (`AGENTS.md` & `docs/architecture/engineering_constitution.md`)
- KORTEX OS Platform Principles (`docs/architecture/platform_principles.md`)
- Universal Shared Domain Models (`docs/architecture/shared_domain_models.md`)
- Universal Asset System (`docs/architecture/asset_system.md`)

---

## 1. Purpose

This document defines the formal **Marketplace Architecture** for KORTEX OS.

The KORTEX Marketplace provides the distribution ecosystem enabling enterprise teams, developers, and third-party vendors to publish, discover, verify, license, install, and update platform extension assets across local, enterprise, and public repositories.

---

## 2. Marketplace Philosophy

1. **Local-First & Offline-First Distribution**: Installed assets operate 100% locally. Internet connection is required only for initial package downloading or synchronization. Offline manual package installation (`.kortex-*`) is fully supported.
2. **Zero Code Vulnerabilities**: Non-adapter marketplace assets (Recipes, Templates, Profiles, Knowledge Packs) are strictly declarative and contain zero executable code.
3. **Cryptographic Trust Hierarchy**: All published marketplace packages MUST be signed using Ed25519 digital signatures and verified against SHA256 checksums before installation.
4. **Federated Repositories**: Supports Public Marketplace, Enterprise Private Marketplace, and Local Repository distribution models.

---

## 3. Supported Asset Types

The Marketplace distributes 11 canonical asset types:
1. **Recipes** (`.kortex-recipe`): Zero-code business automation workflows.
2. **Templates** (`.kortex-template`): Declarative document layouts and schemas.
3. **Adapters** (`.kortex-adapter`): Sandboxed document and connector adapters.
4. **Modules** (`.kortex-module`): Complete domain business modules (HR, Payroll, Inventory, CRM).
5. **Knowledge Packs** (`.kortex-knowledge`): Declarative domain ontologies and reference knowledge graphs.
6. **Connectors** (`.kortex-connector`): External system integration channel drivers.
7. **Themes** (`.kortex-theme`): UI color tokens, layout themes, and visual styles.
8. **Business Packs** (`.kortex-business`): Pre-configured multi-module business solution bundles.
9. **AI Packs** (`.kortex-ai`): Fine-tuned local model prompts, agent configurations, and RAG context bundles.
10. **Document Packs** (`.kortex-docpack`): Industry-specific document operation profile bundles.
11. **Workflow Packs** (`.kortex-workflow`): Pre-compiled workflow state machine definition libraries.

---

## 4. Publishing Pipeline

1. **Package Assembly**: Developer builds asset directory and runs `kortex package` CLI tool.
2. **Manifest Generation**: Generates `KortexAssetManifest` containing package metadata, version, dependencies, and capability declarations.
3. **Checksum & Signature**: Computes SHA256 payload digest and applies publisher's Ed25519 digital signature (`signature.sig`).
4. **Upload**: Submits signed archive to target Marketplace repository.

---

## 5. Digital Signatures & Signing

- **Algorithm**: Ed25519 public-key cryptography.
- **Publisher Identity**: Publishers register public keys in Marketplace registry.
- **Verification**: Local installer verifies package signature against publisher public key before extraction.

---

## 6. Verification Pipeline

Upon receiving or importing an asset package, the local verification engine executes the 6-stage Verification Pipeline specified in `asset_system.md`: File Structure Check $\rightarrow$ SHA256 Digest Verification $\rightarrow$ Ed25519 Digital Signature Verification $\rightarrow$ Static Code Analysis $\rightarrow$ Schema Validation $\rightarrow$ DAG Dependency Graph Verification.

---

## 7. Licensing Architecture

Supports flexible licensing models declared in `KortexAssetManifest`:
- **Open Source** (MIT, Apache-2.0, BSD).
- **Free Enterprise** (Unrestricted local use).
- **Commercial Perpetual** (Licensed for specific local installation nodes).
- **Subscription License** (Periodically renewed license token verified locally).

---

## 8. Pricing & Monetization

Marketplace registry supports Free, One-time Commercial Purchase, and Tiered Enterprise Subscription pricing models, managed via signed cryptographic license tokens.

---

## 9. Marketplace Deployment Models

```
┌─────────────────────────┐   ┌─────────────────────────┐   ┌─────────────────────────┐
│   Public Marketplace    │   │  Enterprise Marketplace │   │    Local Repository     │
│ (Global Vendor Hub)     │   │ (Private Corporate Hub) │   │ (Offline Local Storage) │
└────────────┬────────────┘   └────────────┬────────────┘   └────────────┬────────────┘
             │                             │                             │
             └─────────────────────────────┼─────────────────────────────┘
                                           │
                                           ▼
                            ┌──────────────────────────────┐
                            │ Local Asset System Installer │
                            └──────────────────────────────┘
```

- **Public Marketplace**: Global public repository for open-source and commercial vendor extensions.
- **Enterprise Private Marketplace**: Self-hosted private repository for corporate internal modules and security-restricted assets.
- **Local Repository**: Offline folder/disk storage repository for air-gapped environments.

---

## 10. Version Compatibility & Dependency Resolution

- Enforces Semantic Versioning 2.0.0 (`MAJOR.MINOR.PATCH`).
- Resolves package dependency trees using topological sort DAG algorithms.
- Mismatches in minimum Kernel or engine versions cause atomic installation rejection.

---

## 11. Community Features (Reviews, Ratings, Downloads)

Public and Enterprise Marketplaces support telemetry indexing for community ratings (1–5 stars), peer reviews, download metrics, and publisher verification badges.

---

## 12. Updates, Rollback & Deprecation

- **Updates**: Automated checking for new SemVer updates matching declared compatible ranges.
- **Rollbacks**: Atomic rollback restores previous stable asset snapshot upon update failure.
- **Deprecation**: Deprecated assets marked `DEPRECATED` in registry, generating warnings without breaking existing executions.

---

## 13. Ownership Transfer & Security

Assets support cryptographic ownership transfers by updating publisher public key signatures in the Marketplace registry. Security vulnerability scans automatically flag and revoke compromised package checksums.

---

## 14. Enterprise Distribution & Offline Air-Gapped Installation

Supports 100% air-gapped offline installations. Admins export verified `.kortex-*` packages to USB/local media and install via `AssetSystem` CLI without internet connectivity.

---

## 15. Acceptance Criteria

- ✓ **Universal Support**: Supports all 11 canonical asset types.
- ✓ **Cryptographic Security**: Ed25519 signature and SHA256 checksum validation mandatory.
- ✓ **Offline & Air-Gapped**: Complete local installation without cloud API calls.
- ✓ **Federated Repositories**: Supports Public, Enterprise Private, and Local distribution models.
