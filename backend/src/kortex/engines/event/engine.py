"""
KORTEX Event Engine.

High-performance async/sync event bus providing event-driven communication,
priority routing, subscriber error isolation, and future distributed broker readiness.
"""

from __future__ import annotations

import asyncio
import datetime
import enum
import inspect
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from kortex.core.base_engine import BaseEngine, EngineState

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel


class EventPriority(int, enum.Enum):
    """Priority levels for event dispatching."""

    CRITICAL = 0
    HIGH = 10
    NORMAL = 50
    LOW = 100


class Event(BaseModel):
    """Structured domain event payload."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique event identifier")
    topic: str = Field(description="Event topic string, e.g. 'payroll.calculated'")
    payload: dict[str, Any] = Field(default_factory=dict, description="Event data dictionary")
    sender: str = Field(default="system", description="Identifier of event publisher")
    priority: EventPriority = Field(default=EventPriority.NORMAL, description="Event priority level")
    timestamp: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
        description="Event creation timestamp",
    )
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Distributed tracing ID")
    headers: dict[str, str] = Field(default_factory=dict, description="Event header attributes")


class EventDeliveryResult(BaseModel):
    """Result status of dispatching an event to subscribers."""

    event_id: str
    topic: str
    subscribers_notified: int = 0
    subscribers_failed: int = 0
    errors: list[str] = Field(default_factory=list)


SubscriberCallback = Callable[[Event], Any]


class EventSubscription:
    """Subscription record containing handler, topic, priority, and unique ID."""

    def __init__(
        self,
        subscription_id: str,
        topic: str,
        handler: SubscriberCallback,
        priority: EventPriority = EventPriority.NORMAL,
        subscriber_name: str = "anonymous",
    ) -> None:
        self.id = subscription_id
        self.topic = topic
        self.handler = handler
        self.priority = priority
        self.subscriber_name = subscriber_name


class EventEngine(BaseEngine):
    """Event Bus System Engine."""

    def __init__(self) -> None:
        super().__init__()
        # topic -> list of EventSubscription sorted by priority
        self._subscriptions: dict[str, list[EventSubscription]] = {}
        self._wildcard_subscriptions: list[EventSubscription] = []
        self._event_count: int = 0
        self._failed_delivery_count: int = 0

    @property
    def name(self) -> str:
        return "event"

    async def initialize(self, kernel: Kernel) -> None:
        """Initialize the Event Engine."""
        self._set_state(EngineState.INITIALIZING)
        self.logger.info("Initializing Event Engine...")
        self._set_state(EngineState.READY)

    async def start(self) -> None:
        """Start the Event Engine."""
        self.ensure_state(EngineState.READY)
        self._set_state(EngineState.RUNNING)
        self.logger.info("Event Engine running.")

    async def health_check(self) -> dict[str, Any]:
        """Diagnostic health check."""
        total_subscribers = sum(len(subs) for subs in self._subscriptions.values()) + len(self._wildcard_subscriptions)
        return {
            "engine": self.name,
            "status": "healthy" if self.state == EngineState.RUNNING else "unhealthy",
            "topics_count": len(self._subscriptions),
            "total_subscribers": total_subscribers,
            "events_published_total": self._event_count,
            "failed_deliveries_total": self._failed_delivery_count,
        }

    async def stop(self) -> None:
        """Stop the Event Engine."""
        self._set_state(EngineState.STOPPING)
        self._subscriptions.clear()
        self._wildcard_subscriptions.clear()
        self._set_state(EngineState.STOPPED)
        self.logger.info("Event Engine stopped.")

    # -- Subscription API ---------------------------------------------------

    def subscribe(
        self,
        topic: str,
        handler: SubscriberCallback,
        priority: EventPriority = EventPriority.NORMAL,
        subscriber_name: str = "anonymous",
    ) -> str:
        """Subscribe a handler function to a topic.

        Args:
            topic: Topic string or '*' for all events.
            handler: Sync or Async callable receiving an Event instance.
            priority: Priority level. Lower value executes first.
            subscriber_name: Human readable subscriber identity.

        Returns:
            Subscription ID string.
        """
        sub_id = str(uuid.uuid4())
        sub = EventSubscription(sub_id, topic, handler, priority, subscriber_name)

        if topic == "*":
            self._wildcard_subscriptions.append(sub)
            self._wildcard_subscriptions.sort(key=lambda s: s.priority.value)
        else:
            if topic not in self._subscriptions:
                self._subscriptions[topic] = []
            self._subscriptions[topic].append(sub)
            self._subscriptions[topic].sort(key=lambda s: s.priority.value)

        self.logger.debug("Subscribed '%s' to topic '%s' (ID: %s)", subscriber_name, topic, sub_id)
        return sub_id

    def unsubscribe(self, subscription_id: str) -> bool:
        """Unsubscribe a subscriber by its subscription ID."""
        # Check wildcard list
        for i, sub in enumerate(self._wildcard_subscriptions):
            if sub.id == subscription_id:
                self._wildcard_subscriptions.pop(i)
                self.logger.debug("Unsubscribed wildcard subscription ID: %s", subscription_id)
                return True

        # Check topic lists
        for topic, subs in self._subscriptions.items():
            for i, sub in enumerate(subs):
                if sub.id == subscription_id:
                    subs.pop(i)
                    self.logger.debug(
                        "Unsubscribed '%s' from topic '%s' (ID: %s)", sub.subscriber_name, topic, subscription_id
                    )
                    return True

        return False

    # -- Event Publishing API -----------------------------------------------

    async def publish(
        self,
        topic: str,
        payload: dict[str, Any] | None = None,
        sender: str = "system",
        priority: EventPriority = EventPriority.NORMAL,
        headers: dict[str, str] | None = None,
    ) -> EventDeliveryResult:
        """Asynchronously publish an event to all registered topic subscribers.

        Includes complete error isolation: handler exceptions are caught and reported
        without breaking other subscribers.
        """
        event = Event(
            topic=topic,
            payload=payload or {},
            sender=sender,
            priority=priority,
            headers=headers or {},
        )
        return await self.publish_event(event)

    async def publish_event(self, event: Event) -> EventDeliveryResult:
        """Publish a pre-constructed Event object asynchronously."""
        self._event_count += 1
        result = EventDeliveryResult(event_id=event.id, topic=event.topic)

        # Target subscribers + Wildcard subscribers
        target_subs: list[EventSubscription] = list(self._subscriptions.get(event.topic, []))
        target_subs.extend(self._wildcard_subscriptions)
        # Sort combined list by priority
        target_subs.sort(key=lambda s: s.priority.value)

        if not target_subs:
            self.logger.debug("No subscribers for event topic '%s'", event.topic)
            return result

        for sub in target_subs:
            try:
                if inspect.iscoroutinefunction(sub.handler):
                    await sub.handler(event)
                else:
                    sub.handler(event)
                result.subscribers_notified += 1
            except Exception as exc:
                result.subscribers_failed += 1
                self._failed_delivery_count += 1
                error_msg = f"Subscriber '{sub.subscriber_name}' failed on event {event.topic}: {exc}"
                result.errors.append(error_msg)
                self.logger.error("Error isolation caught subscriber exception: %s", error_msg, exc_info=True)

        return result

    def publish_sync(
        self,
        topic: str,
        payload: dict[str, Any] | None = None,
        sender: str = "system",
        priority: EventPriority = EventPriority.NORMAL,
    ) -> EventDeliveryResult:
        """Synchronously publish an event (invoking sync handlers immediately).

        For async handlers in sync publish mode, schedules them on the running event loop if active.
        """
        event = Event(
            topic=topic,
            payload=payload or {},
            sender=sender,
            priority=priority,
        )
        self._event_count += 1
        result = EventDeliveryResult(event_id=event.id, topic=event.topic)

        target_subs: list[EventSubscription] = list(self._subscriptions.get(event.topic, []))
        target_subs.extend(self._wildcard_subscriptions)
        target_subs.sort(key=lambda s: s.priority.value)

        for sub in target_subs:
            try:
                if inspect.iscoroutinefunction(sub.handler):
                    try:
                        loop = asyncio.get_running_loop()

                        # notification. Retaining task handles would change Event
                        # Engine delivery semantics; out of scope for a lint fix.
                        loop.create_task(sub.handler(event))  # noqa: RUF006
                    except RuntimeError:
                        asyncio.run(sub.handler(event))
                else:
                    sub.handler(event)
                result.subscribers_notified += 1
            except Exception as exc:
                result.subscribers_failed += 1
                self._failed_delivery_count += 1
                error_msg = f"Subscriber '{sub.subscriber_name}' failed in sync dispatch: {exc}"
                result.errors.append(error_msg)
                self.logger.error("Error isolation caught sync subscriber exception: %s", error_msg, exc_info=True)

        return result
