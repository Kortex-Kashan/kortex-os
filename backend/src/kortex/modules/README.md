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
