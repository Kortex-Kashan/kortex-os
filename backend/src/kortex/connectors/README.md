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
