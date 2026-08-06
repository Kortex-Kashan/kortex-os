"""
Unit tests for Event Engine.
"""

import pytest

from kortex.engines.event.engine import Event, EventEngine, EventPriority


@pytest.mark.asyncio
async def test_event_engine_subscribe_and_publish_async() -> None:
    event_bus = EventEngine()
    received_events = []

    async def handle_invoice(event: Event) -> None:
        received_events.append(event)

    sub_id = event_bus.subscribe("invoice.created", handle_invoice, subscriber_name="test_sub")
    assert sub_id is not None

    result = await event_bus.publish("invoice.created", payload={"id": "INV-100"})
    assert result.subscribers_notified == 1
    assert result.subscribers_failed == 0
    assert len(received_events) == 1
    assert received_events[0].payload["id"] == "INV-100"


@pytest.mark.asyncio
async def test_event_engine_unsubscribe() -> None:
    event_bus = EventEngine()
    received_events = []

    def sync_handler(event: Event) -> None:
        received_events.append(event)

    sub_id = event_bus.subscribe("order.placed", sync_handler)
    res1 = await event_bus.publish("order.placed", payload={"order_id": 1})
    assert res1.subscribers_notified == 1

    unsub_success = event_bus.unsubscribe(sub_id)
    assert unsub_success is True

    res2 = await event_bus.publish("order.placed", payload={"order_id": 2})
    assert res2.subscribers_notified == 0
    assert len(received_events) == 1


@pytest.mark.asyncio
async def test_event_engine_priority_execution_order() -> None:
    event_bus = EventEngine()
    execution_order = []

    def low_priority_handler(event: Event) -> None:
        execution_order.append("LOW")

    def high_priority_handler(event: Event) -> None:
        execution_order.append("HIGH")

    def critical_priority_handler(event: Event) -> None:
        execution_order.append("CRITICAL")

    event_bus.subscribe("system.alert", low_priority_handler, priority=EventPriority.LOW)
    event_bus.subscribe("system.alert", high_priority_handler, priority=EventPriority.HIGH)
    event_bus.subscribe("system.alert", critical_priority_handler, priority=EventPriority.CRITICAL)

    await event_bus.publish("system.alert")
    assert execution_order == ["CRITICAL", "HIGH", "LOW"]


@pytest.mark.asyncio
async def test_event_engine_error_isolation() -> None:
    event_bus = EventEngine()
    successful_calls = []

    def failing_handler(event: Event) -> None:
        raise ValueError("Handler crash!")

    def working_handler(event: Event) -> None:
        successful_calls.append(event.topic)

    event_bus.subscribe("data.sync", failing_handler, priority=EventPriority.HIGH, subscriber_name="failing")
    event_bus.subscribe("data.sync", working_handler, priority=EventPriority.LOW, subscriber_name="working")

    result = await event_bus.publish("data.sync", payload={"data": 123})

    assert result.subscribers_notified == 1
    assert result.subscribers_failed == 1
    assert len(result.errors) == 1
    assert "failing" in result.errors[0]
    assert len(successful_calls) == 1


@pytest.mark.asyncio
async def test_event_engine_wildcard_subscription() -> None:
    event_bus = EventEngine()
    wildcard_events = []

    def wildcard_handler(event: Event) -> None:
        wildcard_events.append(event.topic)

    event_bus.subscribe("*", wildcard_handler)

    await event_bus.publish("user.login")
    await event_bus.publish("user.logout")

    assert wildcard_events == ["user.login", "user.logout"]
