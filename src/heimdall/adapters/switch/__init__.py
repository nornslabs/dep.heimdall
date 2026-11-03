"""Switch Rooms delivery through its Mattermost-compatible chat API."""

from heimdall.adapters.switch.ports import RoomEvent, RoomPort, RoomPost
from heimdall.adapters.switch.runtime import RoomEventHandler, run_inbound_events
from heimdall.adapters.switch.sender import SwitchSender

__all__ = [
    "RoomEvent",
    "RoomEventHandler",
    "RoomPort",
    "RoomPost",
    "SwitchSender",
    "run_inbound_events",
]
