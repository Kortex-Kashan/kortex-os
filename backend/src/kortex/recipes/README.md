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
