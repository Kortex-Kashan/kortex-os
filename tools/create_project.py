#!/usr/bin/env python3
"""
KORTEX OS — Project Scaffolding Tool
=====================================

The first tool in the KORTEX Developer Toolkit.

Generates the complete KORTEX OS project directory structure in an idempotent
manner. Safe to run multiple times — never overwrites existing files or
directories.

Usage:
    python tools/create_project.py                  # Auto-detect project root
    python tools/create_project.py --root /path      # Explicit project root
    python tools/create_project.py --dry-run         # Preview without changes
    python tools/create_project.py --verbose         # Show all operations

Architecture:
    FileEntry           — Immutable specification for a single file
    ScaffoldReport      — Aggregated results of a scaffolding run
    ContentTemplates    — Single source of truth for all generated file contents
    ProjectLayout       — Defines the complete directory tree and file manifest
    SafeFileWriter      — Idempotent writer (creates only; never overwrites)
    ProjectScaffolder   — Top-level orchestrator

Design Principles:
    - Single Responsibility: each class owns one concern
    - Open/Closed: new dirs/files are added to ProjectLayout, not writer logic
    - Dependency Inversion: ProjectScaffolder depends on abstractions
    - Idempotent: every operation checks existence before acting
    - Zero Dependencies: stdlib only (pathlib, argparse, logging, dataclasses)

Compatibility:
    Python 3.12+ · Windows · macOS · Linux
"""

from __future__ import annotations

import argparse
import logging
import sys
import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KORTEX_VERSION = "0.1.0"

# Ensure stdout can handle Unicode on Windows (cp1252 chokes on box-drawing).
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ANSI escape codes for terminal styling.
# Gracefully degraded when the output stream is not a TTY.
_IS_TTY = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


class _Style:
    """Terminal ANSI colour helpers.  All codes collapse to empty strings when
    stdout is not a TTY (e.g. piped to a file or CI log)."""

    GREEN: str = "\033[92m" if _IS_TTY else ""
    RED: str = "\033[91m" if _IS_TTY else ""
    DIM: str = "\033[2m" if _IS_TTY else ""
    CYAN: str = "\033[96m" if _IS_TTY else ""
    YELLOW: str = "\033[93m" if _IS_TTY else ""
    BOLD: str = "\033[1m" if _IS_TTY else ""
    RESET: str = "\033[0m" if _IS_TTY else ""


Style = _Style()

logger = logging.getLogger("kortex.scaffolder")

# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileEntry:
    """Immutable specification for a single file to be created.

    Attributes:
        relative_path: Path relative to the project root.
        content: Text content to write.  Empty string for marker files.
        description: Human-readable purpose (used in logging only).
    """

    relative_path: str
    content: str = ""
    description: str = ""


@dataclass
class ScaffoldReport:
    """Aggregated results of a scaffolding run.

    Collected by ``ProjectScaffolder.scaffold()`` and presented to the
    operator at completion.
    """

    directories_created: int = 0
    directories_existed: int = 0
    files_created: int = 0
    files_skipped: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """True when no errors were recorded."""
        return len(self.errors) == 0


# ---------------------------------------------------------------------------
# Content Templates
# ---------------------------------------------------------------------------


class ContentTemplates:
    """Single source of truth for every generated file's content.

    All template methods are pure functions (no side-effects) and return
    UTF-8 text ready to be written to disk.
    """

    # -- Python package files -----------------------------------------------

    @staticmethod
    def kortex_init() -> str:
        """Root ``__init__.py`` for the kortex package."""
        return textwrap.dedent(f'''\
            """
            KORTEX OS — Local-First AI Business Operating System.

            KORTEX is a Local-First AI Business Operating System where AI,
            Business Recipes, Organizational Knowledge, Connectors and Modules
            work together as one unified platform.
            """

            __version__ = "{KORTEX_VERSION}"
        ''')

    @staticmethod
    def package_init(docstring: str) -> str:
        """Generic ``__init__.py`` with a module-level docstring."""
        return f'"""{docstring}"""\n'

    @staticmethod
    def conftest() -> str:
        """Shared pytest fixtures and configuration."""
        return textwrap.dedent('''\
            """
            KORTEX Test Suite — Shared Fixtures and Configuration.

            This module is automatically loaded by pytest before every test session.
            Place project-wide fixtures, hooks, and configuration here.
            """

            from __future__ import annotations

            import pytest
        ''')

    @staticmethod
    def gitkeep() -> str:
        """Empty marker to preserve an otherwise-empty directory in Git."""
        return ""

    # -- README templates ---------------------------------------------------

    @staticmethod
    def readme_tools() -> str:
        return textwrap.dedent("""\
            # KORTEX Developer Toolkit

            Developer tools and automation scripts for the KORTEX OS project.

            ## Available Tools

            | Tool | Description |
            |------|-------------|
            | `create_project.py` | Generates the complete project directory structure (idempotent). |

            ## Usage

            ```bash
            # Generate project structure (auto-detects root)
            python tools/create_project.py

            # Preview changes without writing
            python tools/create_project.py --dry-run

            # Verbose output
            python tools/create_project.py --verbose
            ```

            ## Adding New Tools

            Place new developer tools in this directory. Each tool must:

            - Be a self-contained Python script with zero external dependencies.
            - Include a comprehensive module docstring.
            - Support `--help` for usage documentation.
            - Follow KORTEX coding standards (PEP 8, type hints, docstrings).
        """)

    @staticmethod
    def readme_backend() -> str:
        return textwrap.dedent("""\
            # KORTEX Backend

            The Python backend powering KORTEX OS.

            ## Technology Stack

            - **Framework**: FastAPI
            - **ORM**: SQLAlchemy 2.0+ (async)
            - **Validation**: Pydantic v2
            - **Database**: PostgreSQL (via asyncpg)
            - **Migrations**: Alembic
            - **Python**: 3.12+

            ## Directory Structure

            ```
            backend/
            ├── src/
            │   └── kortex/              # Main Python package
            │       ├── core/            # Microkernel runtime
            │       ├── engines/         # 21 System Engines
            │       ├── modules/         # Business Modules
            │       ├── recipes/         # Recipe definitions
            │       ├── connectors/      # External integrations
            │       ├── api/             # FastAPI routers & IPC
            │       └── shared/          # Cross-cutting utilities
            ├── tests/                   # Test suite
            ├── alembic/                 # Database migrations
            └── pyproject.toml           # Package configuration
            ```

            ## Development

            ```bash
            # Install dependencies (with dev extras)
            pip install -e ".[dev]"

            # Run tests
            pytest

            # Lint and format
            ruff check src/ tests/
            ruff format src/ tests/

            # Type check
            mypy src/
            ```
        """)

    @staticmethod
    def readme_core() -> str:
        return textwrap.dedent("""\
            # KORTEX Core

            The microkernel runtime — the foundation of KORTEX OS.

            ## Responsibilities

            - **Kernel**: Central orchestrator that boots engines, manages lifecycle, and routes events.
            - **Configuration**: System-wide configuration management via Pydantic Settings.
            - **Dependency Container**: Inversion-of-control container for engine and service registration.
            - **Base Types**: Core domain types, enums, and value objects.
            - **Exceptions**: Hierarchy of KORTEX-specific exceptions.

            ## Architecture

            The Kernel follows a microkernel pattern:

            ```
            Kernel
              ├── Boot Sequence → Discovers and initializes engines
              ├── Registry → Maps capabilities to engine implementations
              ├── Event Bus → Async pub/sub for cross-engine communication
              └── Lifecycle → Startup, health checks, graceful shutdown
            ```

            All modules and engines communicate exclusively through the Kernel.
            Direct module-to-module imports are prohibited.
        """)

    @staticmethod
    def readme_engines() -> str:
        engines_list = textwrap.dedent("""\
            # KORTEX System Engines

            System engines provide the platform's core capabilities.
            Every engine registers its capabilities in the Kernel Registry
            and communicates through the Kernel Event Bus.

            ## Engine Catalogue

            | Engine | Package | Description |
            |--------|---------|-------------|
            | Boot Engine | `boot` | System startup and initialization sequencing |
            | Configuration Engine | `configuration` | Settings, environment, and runtime configuration |
            | Registry Engine | `registry` | Capability registration and service discovery |
            | Identity Engine | `identity` | Users, workspaces, sessions, and tenant isolation |
            | License Engine | `license` | Commercial license validation and feature gating |
            | Module Engine | `module_engine` | Module lifecycle, loading, and dependency resolution |
            | Event Engine | `event` | Async event bus, pub/sub, and event sourcing |
            | AI Engine | `ai` | LLM orchestration via Ollama, prompt management |
            | Knowledge Engine | `knowledge` | RAG, vector embeddings, organizational knowledge |
            | Connector Engine | `connector` | External system integration management |
            | Tool Engine | `tool` | AI function/tool schema generation from capabilities |
            | Workflow Engine | `workflow` | Recipe execution, state machines, approval queues |
            | Process Intelligence Engine | `process_intelligence` | Execution telemetry and process analytics |
            | Document Intelligence Engine | `document_intelligence` | PDF parsing, OCR, document schema extraction |
            | Communication Engine | `communication` | Notifications, email, messaging integration |
            | Security Engine | `security` | RBAC, encryption, API keys, audit logging |
            | Sentinel | `sentinel` | System health monitoring and integrity checks |
            | Monitoring Engine | `monitoring` | Metrics, dashboards, and alerting |
            | Update Engine | `update` | System updates and version management |
            | Recovery Engine | `recovery` | Disaster recovery and system restoration |
            | Backup Engine | `backup` | Automated backup scheduling and management |

            ## Engine Contract

            Every engine must implement:

            1. **Initialization** — Register capabilities with the Kernel Registry.
            2. **Event Handling** — Subscribe to and publish domain events.
            3. **Health Check** — Report operational status on demand.
            4. **Graceful Shutdown** — Release resources cleanly.
        """)
        return engines_list

    @staticmethod
    def readme_modules() -> str:
        return textwrap.dedent("""\
            # KORTEX Business Modules

            Domain-specific business components that deliver end-user functionality.

            ## Module Architecture

            Every KORTEX module exposes a standard set of facets:

            | Facet | Description |
            |-------|-------------|
            | **Data** | Domain models, database schemas, repositories |
            | **UI** | React components and page definitions |
            | **AI** | AI capabilities exposed to the LLM via the Tool Engine |
            | **Recipes** | Declarative business workflows with approval gates |
            | **Templates** | Document and report templates |
            | **Knowledge** | Domain-specific knowledge for RAG enrichment |
            | **Reports** | Analytical reports and data exports |
            | **Permissions** | RBAC roles, scopes, and access policies |

            ## Design Rules

            - Modules **never** import from other modules directly.
            - All inter-module communication goes through the Kernel Event Bus.
            - Modules register their capabilities in the Kernel Registry.
            - Each module is independently testable and deployable.

            ## Planned Modules

            - **Finance**: Invoices, Purchase Orders, Salary Sheets
            - **HR & Payroll**: Attendance, Leave Management, Payroll Calculation
            - **Operations**: Vehicle Tracking, Incident Reports, Field Operations
        """)

    @staticmethod
    def readme_recipes() -> str:
        return textwrap.dedent("""\
            # KORTEX Business Recipes

            Business Recipes are the heart of KORTEX OS.

            A Recipe is a reusable, declarative business workflow definition that
            automates repetitive tasks with built-in human approval gates.

            ## Philosophy

            > Every repetitive business task should eventually become a reusable Recipe.

            ## Examples

            - Payroll calculation and approval
            - Vehicle tracking report generation
            - Attendance summary compilation
            - Salary sheet generation
            - Invoice creation and dispatch
            - Purchase order approval workflow
            - Meeting minutes generation
            - Incident report filing
            - Leave approval workflow

            ## Recipe Anatomy

            Each recipe defines:
            1. **Trigger** — What initiates the recipe (schedule, event, manual).
            2. **Steps** — Ordered sequence of actions and transformations.
            3. **Approval Gates** — Points requiring human review and authorization.
            4. **Outputs** — Documents, reports, or state changes produced.
            5. **Rollback** — Recovery actions if any step fails.
        """)

    @staticmethod
    def readme_connectors() -> str:
        return textwrap.dedent("""\
            # KORTEX Connectors

            Connectors enable KORTEX to integrate with external systems and services.

            ## Responsibilities

            - Securely authenticate with third-party APIs.
            - Synchronize data bidirectionally.
            - Transform external data into KORTEX domain models.
            - Handle rate limiting, retries, and circuit breaking.

            ## Design Rules

            - Connectors are registered in the Connector Engine.
            - The AI never calls external APIs directly — it discovers connectors
              through the Capability Registry.
            - Each connector is independently configurable and testable.
        """)

    @staticmethod
    def readme_api() -> str:
        return textwrap.dedent("""\
            # KORTEX API Layer

            The presentation layer for the KORTEX backend.

            ## Components

            - **REST Routers** — FastAPI route handlers for HTTP endpoints.
            - **WebSocket Handlers** — Real-time communication channels.
            - **Tauri IPC Adapters** — Bridge between Tauri desktop shell and the Python backend.

            ## Design Rules

            - Routers are thin — they validate input, delegate to engines, and format output.
            - All business logic lives in engines and modules, never in routers.
            - Every endpoint uses Pydantic models for request/response validation.
        """)

    @staticmethod
    def readme_shared() -> str:
        return textwrap.dedent("""\
            # KORTEX Shared Utilities

            Cross-cutting utilities and base abstractions used across the KORTEX system.

            ## Contents

            - **Base Models** — Pydantic base classes with common fields (id, timestamps).
            - **Types** — Shared type aliases, enums, and value objects.
            - **Helpers** — Date/time utilities, string helpers, validation functions.
            - **Protocols** — Shared interface definitions (typing.Protocol).

            ## Rules

            - Code here must have **zero** domain-specific knowledge.
            - No imports from engines, modules, or connectors.
            - Everything must be independently testable.
        """)

    @staticmethod
    def readme_tests() -> str:
        return textwrap.dedent("""\
            # KORTEX Test Suite

            Automated tests for the KORTEX OS backend.

            ## Structure

            ```
            tests/
            ├── conftest.py       # Shared fixtures and pytest configuration
            ├── unit/             # Fast, isolated unit tests
            ├── integration/      # Tests requiring database or services
            └── e2e/              # Full end-to-end workflow tests
            ```

            ## Running Tests

            ```bash
            # Run all tests
            pytest

            # Run only unit tests
            pytest tests/unit/

            # Run with coverage
            pytest --cov=kortex --cov-report=html

            # Run specific markers
            pytest -m "unit"
            pytest -m "not slow"
            ```

            ## Writing Tests

            - Place unit tests in `tests/unit/<engine_or_module>/`.
            - Use `conftest.py` fixtures for database sessions, test clients, etc.
            - Mark slow tests with `@pytest.mark.slow`.
            - Mark integration tests with `@pytest.mark.integration`.
        """)

    @staticmethod
    def readme_desktop() -> str:
        return textwrap.dedent("""\
            # KORTEX Desktop

            The Tauri v2 desktop application — the primary user interface for KORTEX OS.

            ## Technology Stack

            - **Shell**: Tauri v2 (Rust-based lightweight desktop container)
            - **Frontend**: React 18+ with TypeScript
            - **Styling**: TailwindCSS
            - **State Management**: TBD
            - **IPC**: Tauri command bridge → FastAPI backend

            ## Architecture

            The desktop app communicates with the Python backend via:
            1. **Tauri IPC Commands** — For synchronous request/response patterns.
            2. **WebSocket Channels** — For real-time streaming (AI responses, live updates).

            The Python backend runs as a sidecar process managed by the Tauri shell.
        """)

    @staticmethod
    def readme_server() -> str:
        return textwrap.dedent("""\
            # KORTEX Server

            Headless enterprise server runner for KORTEX OS.

            ## Purpose

            Runs the KORTEX backend without the Tauri desktop shell, enabling:

            - Server-based deployment for multi-user enterprise environments.
            - Headless operation for CI/CD pipelines and automated workflows.
            - Docker container deployment.

            ## Usage

            ```bash
            # Start the server
            uvicorn kortex.api.main:app --host 0.0.0.0 --port 8000
            ```
        """)

    @staticmethod
    def readme_docker() -> str:
        return textwrap.dedent("""\
            # KORTEX Docker

            Container configurations for development and production deployment.

            ## Files

            | File | Description |
            |------|-------------|
            | `docker-compose.yml` | Local development stack (PostgreSQL, backend) |
            | `docker-compose.prod.yml` | Production deployment configuration |
            | `Dockerfile.backend` | Python backend container image |

            ## Quick Start

            ```bash
            # Start local development stack
            docker compose up -d

            # View logs
            docker compose logs -f

            # Stop
            docker compose down
            ```
        """)

    @staticmethod
    def readme_docs() -> str:
        return textwrap.dedent("""\
            # KORTEX Documentation

            Comprehensive documentation for the KORTEX OS platform.

            ## Documentation Index

            | Section | Description |
            |---------|-------------|
            | [Architecture](architecture/) | System architecture, design decisions, diagrams |
            | [API](api/) | REST API reference and WebSocket protocol docs |
            | [Development](development/) | Setup guides, coding standards, contribution guide |
            | [Engines](engines/) | Detailed documentation for each system engine |
            | [Modules](modules/) | Business module specifications and user guides |
            | [Recipes](recipes/) | Recipe authoring guide and template reference |
        """)

    @staticmethod
    def readme_docs_section(title: str, description: str) -> str:
        return f"# {title}\n\n{description}\n"

    @staticmethod
    def readme_scripts() -> str:
        return textwrap.dedent("""\
            # KORTEX Scripts

            Development environment setup and build automation scripts.

            ## Planned Scripts

            - `setup_dev.py` — Automated development environment provisioning.
            - `reset_db.py` — Database reset and re-seeding for development.
            - `generate_migration.py` — Alembic migration helper.
        """)


# ---------------------------------------------------------------------------
# Project Layout Definition
# ---------------------------------------------------------------------------


class ProjectLayout:
    """Defines the complete KORTEX OS project directory tree and file manifest.

    To add new directories or files to the project structure, modify the
    ``directories()`` and ``files()`` methods.  No changes to writer or
    scaffolder logic are required (Open/Closed Principle).
    """

    # The 21 KORTEX system engines, ordered by architectural layer.
    ENGINES: tuple[str, ...] = (
        "boot",
        "configuration",
        "registry",
        "identity",
        "license",
        "module_engine",
        "event",
        "ai",
        "knowledge",
        "connector",
        "tool",
        "workflow",
        "process_intelligence",
        "document_intelligence",
        "communication",
        "security",
        "sentinel",
        "monitoring",
        "update",
        "recovery",
        "backup",
    )

    # Human-readable docstrings for each engine __init__.py
    ENGINE_DOCSTRINGS: dict[str, str] = {
        "boot": "KORTEX Boot Engine — System startup and initialization sequencing.",
        "configuration": "KORTEX Configuration Engine — Settings and runtime configuration.",
        "registry": "KORTEX Registry Engine — Capability registration and service discovery.",
        "identity": "KORTEX Identity Engine — Users, workspaces, sessions, and tenants.",
        "license": "KORTEX License Engine — Commercial license validation and feature gating.",
        "module_engine": "KORTEX Module Engine — Module lifecycle, loading, and dependency resolution.",
        "event": "KORTEX Event Engine — Async event bus and pub/sub infrastructure.",
        "ai": "KORTEX AI Engine — LLM orchestration, prompt management, and structured outputs.",
        "knowledge": "KORTEX Knowledge Engine — RAG, vector embeddings, and organizational knowledge.",
        "connector": "KORTEX Connector Engine — External system integration management.",
        "tool": "KORTEX Tool Engine — AI function/tool schema generation from registered capabilities.",
        "workflow": "KORTEX Workflow Engine — Recipe execution, state machines, and approval queues.",
        "process_intelligence": "KORTEX Process Intelligence Engine — Execution telemetry and analytics.",
        "document_intelligence": "KORTEX Document Intelligence Engine — Document parsing and extraction.",
        "communication": "KORTEX Communication Engine — Notifications, email, and messaging.",
        "security": "KORTEX Security Engine — RBAC, encryption, API keys, and audit logging.",
        "sentinel": "KORTEX Sentinel — System health monitoring, deadlock detection, and integrity.",
        "monitoring": "KORTEX Monitoring Engine — Metrics collection, dashboards, and alerting.",
        "update": "KORTEX Update Engine — System updates and version management.",
        "recovery": "KORTEX Recovery Engine — Disaster recovery and system restoration.",
        "backup": "KORTEX Backup Engine — Automated backup scheduling and management.",
    }

    def directories(self) -> list[str]:
        """All directories that must exist, relative to project root."""
        dirs = [
            # Applications
            "apps/desktop",
            "apps/server",
            # Backend source tree
            "backend/src/kortex",
            "backend/src/kortex/core",
            "backend/src/kortex/engines",
            "backend/src/kortex/modules",
            "backend/src/kortex/recipes",
            "backend/src/kortex/connectors",
            "backend/src/kortex/api",
            "backend/src/kortex/shared",
            # Tests
            "backend/tests",
            "backend/tests/unit",
            "backend/tests/integration",
            "backend/tests/e2e",
            # Database migrations
            "backend/alembic/versions",
            # Infrastructure
            "docker",
            # Documentation
            "docs",
            "docs/architecture",
            "docs/api",
            "docs/development",
            "docs/engines",
            "docs/modules",
            "docs/recipes",
            # Automation
            "scripts",
            "tools",
            # Top-level extension folders
            "knowledge",
            "templates",
            "recipes",
            "marketplace",
            "installer",
            "design-system",
            "sdk",
            "shared",
        ]

        # Engine directories
        for engine in self.ENGINES:
            dirs.append(f"backend/src/kortex/engines/{engine}")

        return dirs

    def files(self) -> list[FileEntry]:
        """All files that must exist with their content."""
        entries: list[FileEntry] = []

        # -- Python package init files --------------------------------------
        entries.append(FileEntry(
            "backend/src/kortex/__init__.py",
            ContentTemplates.kortex_init(),
            "Root package with version",
        ))
        entries.append(FileEntry(
            "backend/src/kortex/core/__init__.py",
            ContentTemplates.package_init("KORTEX Core — Microkernel runtime and system foundations."),
            "Core package",
        ))
        entries.append(FileEntry(
            "backend/src/kortex/engines/__init__.py",
            ContentTemplates.package_init("KORTEX System Engines — Platform capability providers."),
            "Engines package",
        ))

        for engine in self.ENGINES:
            docstring = self.ENGINE_DOCSTRINGS.get(
                engine,
                f"KORTEX {engine.replace('_', ' ').title()} Engine.",
            )
            entries.append(FileEntry(
                f"backend/src/kortex/engines/{engine}/__init__.py",
                ContentTemplates.package_init(docstring),
                f"Engine package: {engine}",
            ))

        entries.extend([
            FileEntry(
                "backend/src/kortex/modules/__init__.py",
                ContentTemplates.package_init("KORTEX Business Modules — Domain-specific business components."),
                "Modules package",
            ),
            FileEntry(
                "backend/src/kortex/recipes/__init__.py",
                ContentTemplates.package_init("KORTEX Business Recipes — Declarative workflow definitions."),
                "Recipes package",
            ),
            FileEntry(
                "backend/src/kortex/connectors/__init__.py",
                ContentTemplates.package_init("KORTEX Connectors — External system integrations."),
                "Connectors package",
            ),
            FileEntry(
                "backend/src/kortex/api/__init__.py",
                ContentTemplates.package_init("KORTEX API Layer — FastAPI routers and IPC adapters."),
                "API package",
            ),
            FileEntry(
                "backend/src/kortex/shared/__init__.py",
                ContentTemplates.package_init("KORTEX Shared Utilities — Cross-cutting helpers and base types."),
                "Shared utilities package",
            ),
        ])

        # -- Test infrastructure --------------------------------------------
        entries.extend([
            FileEntry("backend/tests/__init__.py",
                       ContentTemplates.package_init("KORTEX Test Suite."),
                       "Tests package"),
            FileEntry("backend/tests/conftest.py",
                       ContentTemplates.conftest(),
                       "Shared pytest fixtures"),
            FileEntry("backend/tests/unit/__init__.py",
                       ContentTemplates.package_init("KORTEX Unit Tests."),
                       "Unit tests package"),
            FileEntry("backend/tests/integration/__init__.py",
                       ContentTemplates.package_init("KORTEX Integration Tests."),
                       "Integration tests package"),
            FileEntry("backend/tests/e2e/__init__.py",
                       ContentTemplates.package_init("KORTEX End-to-End Tests."),
                       "E2E tests package"),
        ])

        # -- .gitkeep markers -----------------------------------------------
        for path in (
            "backend/tests/unit/.gitkeep",
            "backend/tests/integration/.gitkeep",
            "backend/tests/e2e/.gitkeep",
            "backend/alembic/versions/.gitkeep",
        ):
            entries.append(FileEntry(path, ContentTemplates.gitkeep(), "Git directory marker"))

        # -- README files ---------------------------------------------------
        readme_map: list[tuple[str, str]] = [
            ("tools/README.md", ContentTemplates.readme_tools()),
            ("backend/README.md", ContentTemplates.readme_backend()),
            ("backend/src/kortex/core/README.md", ContentTemplates.readme_core()),
            ("backend/src/kortex/engines/README.md", ContentTemplates.readme_engines()),
            ("backend/src/kortex/modules/README.md", ContentTemplates.readme_modules()),
            ("backend/src/kortex/recipes/README.md", ContentTemplates.readme_recipes()),
            ("backend/src/kortex/connectors/README.md", ContentTemplates.readme_connectors()),
            ("backend/src/kortex/api/README.md", ContentTemplates.readme_api()),
            ("backend/src/kortex/shared/README.md", ContentTemplates.readme_shared()),
            ("backend/tests/README.md", ContentTemplates.readme_tests()),
            ("apps/desktop/README.md", ContentTemplates.readme_desktop()),
            ("apps/server/README.md", ContentTemplates.readme_server()),
            ("docker/README.md", ContentTemplates.readme_docker()),
            ("docs/README.md", ContentTemplates.readme_docs()),
            ("scripts/README.md", ContentTemplates.readme_scripts()),
            # Top-level extension folders READMEs
            ("knowledge/README.md", ContentTemplates.readme_docs_section("KORTEX Organizational Knowledge Base", "Root repository for global organizational knowledge, RAG document assets, domain manuals, and system knowledge graphs.")),
            ("templates/README.md", ContentTemplates.readme_docs_section("KORTEX Document Templates", "System-wide document and report templates (Invoices, POs, Salary Sheets, Incident Reports) used by business recipes and export engines.")),
            ("recipes/README.md", ContentTemplates.readme_docs_section("KORTEX Declarative Recipes", "Declarative business recipe definitions (YAML/JSON workflows) for automated business processes with human approval gates.")),
            ("marketplace/README.md", ContentTemplates.readme_docs_section("KORTEX Marketplace", "KORTEX Module & Recipe Marketplace artifacts, package manifests, and distribution bundles.")),
            ("installer/README.md", ContentTemplates.readme_docs_section("KORTEX Installers & Packaging", "Desktop and server packaging, installer scripts, sidecar setup bundles, and OS-specific distribution binaries.")),
            ("design-system/README.md", ContentTemplates.readme_docs_section("KORTEX Design System", "Shared UI design tokens, component guidelines, TailwindCSS themes, icons, and visual assets for KORTEX applications.")),
            ("sdk/README.md", ContentTemplates.readme_docs_section("KORTEX Software Development Kit", "KORTEX SDK for building custom business modules, connectors, and recipe extensions.")),
            ("shared/README.md", ContentTemplates.readme_docs_section("KORTEX Shared Resources", "Top-level shared resources, shared schemas, cross-language protocol buffers/contracts, and common assets.")),
        ]
        for path, content in readme_map:
            entries.append(FileEntry(path, content, f"README: {path}"))

        # Documentation sub-section READMEs
        doc_sections: dict[str, str] = {
            "architecture": "System architecture diagrams, design decision records, and structural guidelines.",
            "api": "REST API reference, WebSocket protocol documentation, and endpoint specifications.",
            "development": "Development environment setup, coding standards, and contribution workflow.",
            "engines": "Detailed technical documentation for each of the 21 KORTEX system engines.",
            "modules": "Business module specifications, data models, and user-facing documentation.",
            "recipes": "Recipe authoring guide, schema reference, and template catalogue.",
        }
        for section, desc in doc_sections.items():
            title = f"KORTEX {section.replace('_', ' ').title()} Documentation"
            entries.append(FileEntry(
                f"docs/{section}/README.md",
                ContentTemplates.readme_docs_section(title, desc),
                f"Docs section: {section}",
            ))

        return entries


# ---------------------------------------------------------------------------
# File Writer (Idempotent)
# ---------------------------------------------------------------------------


class SafeFileWriter:
    """Idempotent file and directory writer.

    Creates files and directories only when they do not already exist.
    Never modifies, overwrites, or deletes anything.

    Args:
        root: Absolute path to the project root directory.
        dry_run: When True, log what *would* happen but change nothing.
    """

    def __init__(self, root: Path, dry_run: bool = False) -> None:
        self._root = root.resolve()
        self._dry_run = dry_run

    @property
    def root(self) -> Path:
        return self._root

    def ensure_directory(self, relative_path: str) -> bool:
        """Create a directory (and parents) if it does not exist.

        Returns:
            True if the directory was created, False if it already existed.
        """
        target = self._root / relative_path
        if target.is_dir():
            logger.debug("Directory exists: %s", relative_path)
            return False

        if not self._dry_run:
            target.mkdir(parents=True, exist_ok=True)

        logger.debug("Created directory: %s", relative_path)
        return True

    def write_file(self, entry: FileEntry) -> bool:
        """Write a file if it does not already exist.

        Parent directories are created automatically.

        Returns:
            True if the file was created, False if it was skipped.
        """
        target = self._root / entry.relative_path
        if target.exists():
            logger.debug("File exists (skipped): %s", entry.relative_path)
            return False

        if not self._dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(entry.content, encoding="utf-8")

        logger.debug("Created file: %s", entry.relative_path)
        return True


# ---------------------------------------------------------------------------
# Scaffolder (Orchestrator)
# ---------------------------------------------------------------------------


class ProjectScaffolder:
    """Top-level orchestrator that generates the full project structure.

    Composes ``ProjectLayout`` with ``SafeFileWriter`` to produce the
    complete directory tree in a single ``scaffold()`` call.
    """

    def __init__(
        self,
        root: Path,
        *,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> None:
        self._root = root.resolve()
        self._dry_run = dry_run
        self._verbose = verbose
        self._writer = SafeFileWriter(self._root, dry_run=dry_run)
        self._layout = ProjectLayout()
        self._report = ScaffoldReport()

    # -- Output helpers -----------------------------------------------------

    def _timestamp(self) -> str:
        return datetime.now(tz=timezone.utc).strftime("%H:%M:%S")

    def _log_line(self, icon: str, label: str, path: str, color: str) -> None:
        ts = self._timestamp()
        print(f"  {Style.DIM}[{ts}]{Style.RESET} {color}{icon} {label}{Style.RESET}  {path}")

    def _log_created(self, path: str) -> None:
        self._log_line("+", "Created", path, Style.GREEN)

    def _log_exists(self, path: str) -> None:
        if self._verbose:
            self._log_line(".", "Exists ", path, Style.DIM)

    def _log_error(self, path: str, error: str) -> None:
        self._log_line("x", "Error  ", f"{path}  ({error})", Style.RED)

    # -- Header / Footer ----------------------------------------------------

    def _print_header(self) -> None:
        mode = "DRY RUN" if self._dry_run else "CREATE"
        separator = "=" * 58
        print()
        print(f"  {Style.CYAN}{Style.BOLD}{separator}{Style.RESET}")
        print(f"  {Style.CYAN}{Style.BOLD}  KORTEX OS -- Project Scaffolder{Style.RESET}")
        print(f"  {Style.CYAN}{Style.BOLD}{separator}{Style.RESET}")
        print()
        print(f"  {Style.BOLD}Project Root:{Style.RESET}  {self._root}")
        print(f"  {Style.BOLD}Mode:{Style.RESET}          {mode}")
        print()

    def _print_footer(self) -> None:
        r = self._report
        error_color = Style.RED if r.errors else Style.GREEN
        print()
        print(f"  {Style.CYAN}{'=' * 58}{Style.RESET}")
        print(f"  {Style.BOLD}Scaffolding Complete{Style.RESET}")
        print(f"  Directories:  {Style.GREEN}{r.directories_created} created{Style.RESET}, {r.directories_existed} existed")
        print(f"  Files:        {Style.GREEN}{r.files_created} created{Style.RESET}, {r.files_skipped} skipped")
        print(f"  Errors:       {error_color}{len(r.errors)}{Style.RESET}")
        print(f"  {Style.CYAN}{'═' * 58}{Style.RESET}")
        print()

    # -- Core ---------------------------------------------------------------

    def scaffold(self) -> ScaffoldReport:
        """Execute the full scaffolding process.

        Returns:
            A ``ScaffoldReport`` summarising what was created/skipped.
        """
        self._print_header()

        # Phase 1: Directories
        directories = self._layout.directories()
        for rel_path in directories:
            try:
                created = self._writer.ensure_directory(rel_path)
                if created:
                    self._report.directories_created += 1
                    self._log_created(rel_path + "/")
                else:
                    self._report.directories_existed += 1
                    self._log_exists(rel_path + "/")
            except OSError as exc:
                msg = f"Directory '{rel_path}': {exc}"
                self._report.errors.append(msg)
                self._log_error(rel_path, str(exc))

        # Phase 2: Files
        files = self._layout.files()
        for entry in files:
            try:
                created = self._writer.write_file(entry)
                if created:
                    self._report.files_created += 1
                    self._log_created(entry.relative_path)
                else:
                    self._report.files_skipped += 1
                    self._log_exists(entry.relative_path)
            except OSError as exc:
                msg = f"File '{entry.relative_path}': {exc}"
                self._report.errors.append(msg)
                self._log_error(entry.relative_path, str(exc))

        self._print_footer()
        return self._report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_project_root(explicit: str | None) -> Path:
    """Determine the project root directory.

    If *explicit* is given, use it.  Otherwise, resolve from this script's
    location (the parent of the ``tools/`` directory).
    """
    if explicit:
        root = Path(explicit).resolve()
    else:
        root = Path(__file__).resolve().parent.parent

    if not root.is_dir():
        print(f"{Style.RED}Error: Project root does not exist: {root}{Style.RESET}", file=sys.stderr)
        sys.exit(1)

    return root


def _configure_logging(verbose: bool) -> None:
    """Set up the Python logging subsystem."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    """Entry point for the KORTEX project scaffolder."""
    parser = argparse.ArgumentParser(
        prog="create_project",
        description="KORTEX OS — Project Scaffolding Tool. Generates the complete project directory structure.",
    )
    parser.add_argument(
        "--root",
        type=str,
        default=None,
        help="Project root directory (default: auto-detect from script location).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without creating files or directories.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show all operations including skipped files.",
    )

    args = parser.parse_args()

    _configure_logging(args.verbose)

    root = _resolve_project_root(args.root)
    scaffolder = ProjectScaffolder(root, dry_run=args.dry_run, verbose=args.verbose)
    report = scaffolder.scaffold()

    sys.exit(0 if report.success else 1)


if __name__ == "__main__":
    main()
