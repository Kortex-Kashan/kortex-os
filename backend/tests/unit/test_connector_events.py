"""Unit tests for Connector Engine immutable event models (Milestone 7).

Target: 100% test pass rate, 100% line coverage for events.py.
"""

from __future__ import annotations

import datetime

import pytest
from pydantic import ValidationError

from kortex.engines.connector.events import (
    ConnectorActionCompletedEvent,
    ConnectorActionFailedEvent,
    ConnectorActionStartedEvent,
    ConnectorBaseEvent,
    ConnectorDriverRegisteredEvent,
)


def test_connector_base_event_defaults_and_immutability() -> None:
    """Test default event_id, UTC timestamp, and frozen model immutability."""
    evt = ConnectorBaseEvent(event_type="test.event")

    assert evt.event_id.startswith("evt-")
    assert len(evt.event_id) > 10
    assert evt.event_type == "test.event"

    # Verify UTC ISO-8601 timestamp string format
    parsed_dt = datetime.datetime.fromisoformat(evt.timestamp)
    assert parsed_dt.tzinfo is not None

    # Verify immutability (frozen = True)
    with pytest.raises(ValidationError):
        evt.event_type = "mutated.event"  # type: ignore[misc]


def test_connector_action_started_event_canonical_type_enforcement() -> None:
    """Test ConnectorActionStartedEvent canonical event_type enforcement and override rejection."""
    evt = ConnectorActionStartedEvent(
        request_id="req-100",
        profile_id="prof-200",
        action_type="SEND",
        correlation_id="corr-300",
    )

    assert evt.event_type == "connector.action.started"
    assert evt.request_id == "req-100"
    assert evt.profile_id == "prof-200"
    assert evt.action_type == "SEND"
    assert evt.correlation_id == "corr-300"

    # Attempting to override canonical event_type MUST raise ValidationError
    with pytest.raises(ValidationError):
        ConnectorActionStartedEvent(
            request_id="r1",
            profile_id="p1",
            action_type="SEND",
            event_type="invalid.custom.type",  # type: ignore[arg-type]
        )

    with pytest.raises(ValidationError):
        evt.request_id = "req-mutated"  # type: ignore[misc]


def test_connector_action_completed_event_canonical_type_enforcement() -> None:
    """Test ConnectorActionCompletedEvent canonical event_type enforcement and override rejection."""
    evt = ConnectorActionCompletedEvent(
        request_id="req-101",
        profile_id="prof-201",
        action_type="FETCH",
        status="SUCCESS",
        execution_time_ms=12.345,
        correlation_id="corr-301",
    )

    assert evt.event_type == "connector.action.completed"
    assert evt.request_id == "req-101"
    assert evt.profile_id == "prof-201"
    assert evt.action_type == "FETCH"
    assert evt.status == "SUCCESS"
    assert evt.execution_time_ms == 12.345
    assert evt.correlation_id == "corr-301"

    # Attempting to override canonical event_type MUST raise ValidationError
    with pytest.raises(ValidationError):
        ConnectorActionCompletedEvent(
            request_id="r1",
            profile_id="p1",
            action_type="FETCH",
            status="SUCCESS",
            execution_time_ms=1.0,
            event_type="invalid.custom.type",  # type: ignore[arg-type]
        )

    with pytest.raises(ValidationError):
        evt.status = "FAILED"  # type: ignore[misc]


def test_connector_action_failed_event_canonical_type_enforcement() -> None:
    """Test ConnectorActionFailedEvent canonical event_type enforcement and override rejection."""
    evt = ConnectorActionFailedEvent(
        request_id="req-102",
        profile_id="prof-202",
        action_type="PUSH",
        error_message="Connection timed out",
        execution_time_ms=50.12,
    )

    assert evt.event_type == "connector.action.failed"
    assert evt.request_id == "req-102"
    assert evt.profile_id == "prof-202"
    assert evt.action_type == "PUSH"
    assert evt.error_message == "Connection timed out"
    assert evt.execution_time_ms == 50.12
    assert evt.correlation_id is None

    # Attempting to override canonical event_type MUST raise ValidationError
    with pytest.raises(ValidationError):
        ConnectorActionFailedEvent(
            request_id="r1",
            profile_id="p1",
            action_type="PUSH",
            error_message="err",
            execution_time_ms=1.0,
            event_type="invalid.custom.type",  # type: ignore[arg-type]
        )

    with pytest.raises(ValidationError):
        evt.error_message = "Other error"  # type: ignore[misc]


def test_connector_driver_registered_event_canonical_type_and_deep_immutability() -> None:
    """Test ConnectorDriverRegisteredEvent canonical event_type enforcement and tuple deep immutability."""
    actions = ("SEND", "RECEIVE", "FETCH")
    evt = ConnectorDriverRegisteredEvent(
        driver_id="dummy-driver-1",
        driver_name="Dummy Reference Driver",
        version="1.0.0",
        supported_actions=actions,
    )

    assert evt.event_type == "connector.driver.registered"
    assert evt.driver_id == "dummy-driver-1"
    assert evt.driver_name == "Dummy Reference Driver"
    assert evt.version == "1.0.0"
    assert isinstance(evt.supported_actions, tuple)
    assert evt.supported_actions == ("SEND", "RECEIVE", "FETCH")

    # Attempting to override canonical event_type MUST raise ValidationError
    with pytest.raises(ValidationError):
        ConnectorDriverRegisteredEvent(
            driver_id="d1",
            driver_name="dname",
            version="1.0.0",
            event_type="invalid.custom.type",  # type: ignore[arg-type]
        )

    # Verify deep immutability
    with pytest.raises(ValidationError):
        evt.driver_id = "mutated-id"  # type: ignore[misc]

    # Verify serialization model dump
    dump = evt.model_dump()
    assert dump["driver_id"] == "dummy-driver-1"
    assert dump["supported_actions"] == ("SEND", "RECEIVE", "FETCH")
