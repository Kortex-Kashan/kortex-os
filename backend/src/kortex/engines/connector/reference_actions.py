"""
KORTEX Connector Action Reference Integration (Milestone F5).

Exactly two reference `ConnectorActionDescriptor`s, proving the descriptor -> capability ->
dispatch-adapter -> `ConnectorEngine.execute_action()` -> driver chain end-to-end against the
existing, real `HttpRestConnectorDriver` -- deliberately small (one read, one mutation), per the F5
mandate to prove the architecture rather than build a provider catalog.

Semantic domain: a generic "webhook notification" -- sending a notification payload to an external
HTTP endpoint and checking its delivery status -- chosen because it is genuinely provider-
independent (any `ConnectorProfile` whose `options.base_url` points at a real webhook-receiving
endpoint can back these same two capabilities, with the specific provider/driver remaining profile
metadata, never part of the capability name, per D5) and because it is the only semantic pattern
`HttpRestConnectorDriver` -- the only non-test driver in the repository today -- can honestly back
without inventing a new transport or a specific named third-party integration.

Each action's `parameters_schema` intentionally mirrors `HttpRestConnectorDriver`'s own payload
contract (`url`/`body`/`params`) one-for-one -- see `actions.py`'s module docstring for why a
richer, driver-agnostic payload-mapping layer is explicitly out of scope for this milestone.

`url` is always the complete, absolute URL to call -- matching the driver's own established
contract exactly (confirmed against its existing test/integration suites): a `ConnectorProfile`'s
`options.base_url` is documentation/connectivity-test metadata only and is never merged onto an
`ActionRequest`'s own `payload`/`options` by the pipeline, so this module does not claim or rely on
any such merge either.
"""

from __future__ import annotations

from kortex.engines.connector.actions import ConnectorActionDescriptor
from kortex.engines.connector.models import ConnectorActionType

_HTTP_RESPONSE_RETURNS_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "status_code": {"type": "integer", "description": "The HTTP response status code."},
        "headers": {"type": "object", "description": "Allow-listed, non-sensitive response headers."},
        "body": {"description": "The parsed JSON response body, or raw text if not JSON."},
    },
    "required": ["status_code"],
}

WEBHOOK_STATUS_ACTION = ConnectorActionDescriptor(
    capability_name="kortex.connector.notification.webhook.status",
    description=(
        "Check the delivery/status resource of a previously sent webhook notification via its "
        "connector profile. Read-only, idempotent, never mutates external state."
    ),
    connector_action_type=ConnectorActionType.FETCH,
    parameters_schema={
        "properties": {
            "url": {
                "type": "string",
                "minLength": 1,
                "description": "The complete, absolute URL of the status resource to fetch.",
            },
            "params": {"type": "object", "description": "Optional query parameters."},
        },
        "required": ["url"],
    },
    returns_schema=_HTTP_RESPONSE_RETURNS_SCHEMA,
    required_permissions=["connector:execute"],
    is_read_only=True,
    is_idempotent=True,
    resource="webhook",
    action="status",
)

WEBHOOK_SEND_ACTION = ConnectorActionDescriptor(
    capability_name="kortex.connector.notification.webhook.send",
    description=(
        "Send a notification payload to an external service via its connector profile. Mutates "
        "state on the external service; not assumed idempotent (a repeated call is a new "
        "notification, not a safe retry of the same one, unless the target endpoint itself "
        "de-duplicates)."
    ),
    connector_action_type=ConnectorActionType.SEND,
    parameters_schema={
        "properties": {
            "url": {
                "type": "string",
                "minLength": 1,
                "description": "The complete, absolute URL of the notification endpoint to send to.",
            },
            "body": {"type": "object", "description": "The JSON notification payload to send."},
        },
        "required": ["url", "body"],
    },
    returns_schema=_HTTP_RESPONSE_RETURNS_SCHEMA,
    required_permissions=["connector:execute"],
    is_read_only=False,
    is_idempotent=False,
    resource="webhook",
    action="send",
)

REFERENCE_ACTION_DESCRIPTORS: list[ConnectorActionDescriptor] = [WEBHOOK_STATUS_ACTION, WEBHOOK_SEND_ACTION]
