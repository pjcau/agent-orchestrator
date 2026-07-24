from .events import Event, EventBus, EventType

__all__ = ["Event", "EventBus", "EventType", "create_dashboard_app"]


def create_dashboard_app(event_bus=None):
    from .app import create_dashboard_app as _create

    return _create(event_bus)
