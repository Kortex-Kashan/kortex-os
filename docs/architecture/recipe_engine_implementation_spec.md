# Recipe Engine Implementation Specification

Version: 1.0.0

Status: Approved for Implementation

Depends On:

- Phase 1 Foundation
- Storage Engine
- Workflow Engine

Authority:

Engineering Constitution

Phase 2 Architecture

---

# Purpose

Implement the KORTEX Recipe Engine.

The Recipe Engine is responsible for loading, validating, compiling, packaging, installing, upgrading, versioning, and managing Recipes.

The Recipe Engine NEVER executes Recipes.

Recipe execution belongs exclusively to the Workflow Engine.

---

# Responsibilities

The Recipe Engine SHALL:

- Load recipe packages
- Validate recipe structure
- Validate schema
- Validate permissions
- Validate dependencies
- Validate compatibility
- Parse recipe.yaml
- Compile recipe definitions into workflow definitions
- Package recipes
- Install recipes
- Upgrade recipes
- Remove recipes
- Register recipes
- Publish recipe metadata
- Manage versions
- Provide diagnostics

The Recipe Engine SHALL NOT:

- Execute workflows
- Render documents
- Send connectors
- Perform business logic
- Execute AI tasks
- Access databases directly
- Access SQLite directly
- Access PostgreSQL directly
- Access files directly

All persistence MUST use Storage Engine.

---

# Folder Structure

backend/src/kortex/engines/recipe/

__init__.py

engine.py

models.py

exceptions.py

interfaces.py

compiler.py

parser.py

validator.py

registry.py

installer.py

packager.py

loader.py

versioning.py

permissions.py

compatibility.py

diagnostics.py

manifest.py

dsl.py

---

backend/tests/unit/

test_recipe_models.py

test_recipe_parser.py

test_recipe_validator.py

test_recipe_compiler.py

test_recipe_registry.py

test_recipe_loader.py

test_recipe_installer.py

test_recipe_packager.py

test_recipe_permissions.py

test_recipe_compatibility.py

test_recipe_engine.py

---

backend/tests/integration/

test_recipe_engine_integration.py

---

# Engine Responsibilities

## engine.py

Central orchestration only.

Coordinates all internal services.

Contains ZERO parsing logic.

Contains ZERO compilation logic.

Contains ZERO validation logic.

---

## parser.py

Loads:

recipe.yaml

manifest.yaml

schema.yaml

permissions.yaml

Parses YAML only.

Never validates.

---

## validator.py

Validates:

Schema

Permissions

Dependency graph

Compatibility

DSL

Checksums

Digital signature

Validation only.

Never compiles.

---

## compiler.py

Transforms:

Recipe DSL

↓

Workflow Definition

Compiler output must be deterministic.

Same input

↓

Same workflow

Always.

---

## registry.py

Registers recipes inside Kernel Registry.

Supports:

Find

Search

List

Lookup by ID

Lookup by Namespace

Lookup by Version

---

## loader.py

Loads recipe packages.

Supports:

Folder

ZIP

.kortex-recipe

Never installs.

---

## installer.py

Responsible for:

Install

Upgrade

Remove

Rollback

Dependency resolution

Verification

Registration

---

## packager.py

Creates

.kortex-recipe

Generates:

Checksums

Manifest

Package

Signature placeholder

---

## versioning.py

Semantic Versioning

Compatibility

Migration support

Dependency resolution

---

## permissions.py

Validates

permissions.yaml

Capability access

Least privilege

---

## compatibility.py

Validates

Kernel

Workflow

Document

Connector

Storage

Profile

Module

Versions

---

## diagnostics.py

Implements

IEngineDiagnostics

health()

metrics()

diagnostics()

status()

version()

capabilities()

---

## manifest.py

Represents

manifest.yaml

Only.

---

## dsl.py

Represents

Recipe DSL

Only.

No execution.

---

# Interfaces

Implement:

IRecipeEngine

IRecipeParser

IRecipeCompiler

IRecipeValidator

IRecipeRegistry

IRecipeLoader

IRecipeInstaller

IRecipePackager

IVersionResolver

IPermissionValidator

ICompatibilityValidator

---

# Pydantic Models

Implement:

RecipeManifest

RecipeDefinition

RecipeMetadata

RecipeInput

RecipeStep

RecipeOutput

RecipeSettings

RecipePermission

RecipeCompatibility

RecipeDependency

RecipeProfile

RecipePackage

RecipeCompilationResult

RecipeValidationResult

RecipeInstallationResult

RecipeUpgradeResult

RecipeRemovalResult

---

# Exceptions

Implement:

RecipeError

RecipeValidationError

RecipeCompilationError

RecipeCompatibilityError

RecipePermissionError

RecipeDependencyError

RecipeInstallationError

RecipePackageError

RecipeSignatureError

RecipeVersionError

---

# Kernel Integration

Register capabilities:

kortex.recipe.load

kortex.recipe.validate

kortex.recipe.compile

kortex.recipe.install

kortex.recipe.remove

kortex.recipe.upgrade

kortex.recipe.package

kortex.recipe.search

kortex.recipe.list

kortex.recipe.info

No direct engine calls.

---

# Storage Integration

Storage Engine only.

Use:

IDataStore

IFileStore

IObjectStore

ICacheStore

No direct filesystem access.

---

# Workflow Integration

Output ONLY:

Workflow Definition

Never execute.

---

# Security Rules

Reject:

Python

JavaScript

SQL

PowerShell

Shell

Executable files

DLL

Unknown assets

Unsigned marketplace packages

Invalid checksum

Unknown capabilities

Unknown permissions

---

# Package Support

Support:

.kortex-recipe

Folder

Development Mode

Marketplace Package

---

# Required Tests

Parser

Validator

Compiler

Installer

Upgrade

Rollback

Registry

Permission Validation

Compatibility Validation

Package Generation

Manifest Validation

DSL Validation

Signature Validation

Dependency Resolution

Kernel Registration

Storage Integration

Diagnostics

Target:

100% tests passing

Minimum 90% coverage for every core file

---

# Forbidden

Do NOT implement:

Workflow execution

Scheduler

Document rendering

Connector execution

Business modules

REST API

GUI

CLI

AI generation

Marketplace server

Cloud synchronization

Database-specific logic

---

# Completion Report

At completion provide exactly:

1. Files Created

2. Files Modified

3. Tests Added

4. Coverage By File

5. Manual Verification

6. Architecture Compliance

7. Open Risks

8. Remaining Uncovered Lines

9. Next Milestone

Stop after Recipe Engine implementation.

Wait for review.

Do not continue to Document Engine.