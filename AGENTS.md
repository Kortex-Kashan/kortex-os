# KORTEX OS - AI Engineering Constitution

Version: 1.0

This document governs every AI assistant, automation agent, and developer working inside the KORTEX OS repository.

This repository has a single architectural authority.

Architecture Authority:
Chief Architect: KASHAN

Implementation Authority:
Software Engineers (Antigravity, Claude, Gemini, Cursor, Copilot, Humans)

Implementation engineers may implement architecture.

They may NOT redesign architecture.

---

# Mission

KORTEX OS is an AI-powered Local-First Business Operating System.

Primary goals:

- Offline first
- Enterprise ready
- Modular
- Event driven
- Extensible
- Maintainable
- Human supervised AI

Everything inside this repository must support these goals.

---

# Golden Rule

Never redesign the system unless explicitly instructed by the Chief Architect.

Implementation is preferred over redesign.

Finish existing work before proposing improvements.

Build the boat first.

Optimize later.

---

# Architecture

The architecture already exists.

Do not replace it.

Do not migrate frameworks.

Do not change folder structures.

Do not rename engines.

Do not invent new architectural layers.

Only extend existing architecture.

---

# Repository Ownership

Root folders are permanent.

No AI may delete or rename:

apps/

backend/

design-system/

docker/

docs/

installer/

knowledge/

marketplace/

recipes/

scripts/

sdk/

shared/

templates/

tools/

.kortex/

---

# Core Principles

KORTEX is:

Local First

Modular

Event Driven

Capability Based

Clean Architecture

SOLID

Domain Driven

Explicit

Typed

Tested

Documented

---

# Kernel

The Kernel contains NO business logic.

Its responsibilities are only:

Boot

Configuration

Registry

Events

Lifecycle

Dependency Injection

Service Discovery

Shutdown

Nothing else.

---

# Engines

Engines are infrastructure.

They never contain business rules.

Current engines include:

Boot Engine

Configuration Engine

Registry Engine

Event Engine

Future engines shall follow the same design.

---

# Modules

Business logic belongs ONLY inside modules.

Modules communicate through:

Capabilities

Events

Never through direct imports.

---

# Database

SQLite is default.

PostgreSQL is supported.

Changing database providers must never require business logic changes.

---

# Dependency Rules

Outer layers depend inward.

Never reverse dependencies.

Never allow circular imports.

Use interfaces whenever appropriate.

---

# Events

Events must remain decoupled.

Publishing an event must never depend on knowing who receives it.

Subscribers must fail independently.

One subscriber crashing must never crash the system.

---

# Dependency Injection

Global variables are forbidden.

Singletons must be registered inside the IoC container.

Construction must happen through dependency injection.

---

# Configuration

Configuration belongs inside Configuration Engine.

Never hardcode:

Paths

Secrets

API Keys

Passwords

Database URLs

Environment specific values

---

# Security

Security comes after functionality.

Never prematurely add authentication.

Never add encryption before requested.

Never add RBAC before Phase 2.

---

# Performance

Prefer readable code.

Optimize only after profiling.

Avoid premature optimization.

---

# Logging

No print() statements.

Infrastructure uses structured logging.

Errors include context.

Sensitive information must never be logged.

---

# Testing

Every feature requires tests.

Minimum:

Unit tests

Integration tests when applicable

Regression tests for fixed bugs

Never merge untested code.

---

# Documentation

Every public class requires documentation.

Every engine requires README documentation.

Every architectural decision belongs inside:

.kortex/decisions.md

---

# Git

Commit frequently.

Small commits.

Descriptive messages.

No generated files.

Never commit:

.venv/

**pycache**/

node_modules/

build/

dist/

---

# AI Rules

AI assistants may:

Implement requested work

Refactor code

Improve readability

Write tests

Write documentation

Fix bugs

They may NOT:

Redesign architecture

Replace frameworks

Rename engines

Invent new repositories

Change roadmap

Skip tests

Skip documentation

---

# Review Checklist

Before considering work complete:

✓ Architecture preserved

✓ Tests passing

✓ Documentation updated

✓ No circular dependencies

✓ No duplicated logic

✓ No hardcoded configuration

✓ No business logic in infrastructure

✓ Local-first philosophy preserved

✓ Event-driven philosophy preserved

✓ Clean Architecture preserved

---

# Roadmap Discipline

The roadmap is sequential.

Never implement future phases early.

Never pull Phase 4 work into Phase 2.

Never add "nice-to-have" features ahead of the current milestone.

Finish the current milestone before moving forward.

---

# Final Rule

When uncertain:

Do not redesign.

Ask.

Implementation is preferred over invention.
