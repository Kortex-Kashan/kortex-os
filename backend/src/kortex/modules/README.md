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

## Implemented Pilot Modules

- **Finance** (`kortex.modules.finance`): Invoices, Purchase Orders, Salary Sheets
- **HR & Payroll** (`kortex.modules.hr_payroll`): Employee Master, Attendance & Overtime, Leave Quotas & Balances, Monthly Payroll Runs, Payslips
- **Operations** (`kortex.modules.operations`): Fleet Vehicle Master, Driver Assignment, Odometer & Location Tracking History, Incident Management & Terminal Closure

## Future Modules

- **Field Operations**: Work Orders, Route Optimization, Mobile Check-in (Phase 6 expansion)
