"""Public interface of the triage module.

Other modules may import only from this file.
"""

from heimdall.modules.triage._thread import get_switch_thread_id, save_switch_thread_id
from heimdall.modules.triage._webhooks import receive_webhook
from heimdall.modules.triage.domain import (
    EventKind,
    Facts,
    PrEvent,
    PrSnapshot,
    PrState,
    Rejected,
    Transition,
)
from heimdall.modules.triage.service import (
    get_pr_snapshot,
    handle_event,
    handle_webhook_delivery,
    on_step_done,
    on_step_done_in_transaction,
)

__all__ = [
    "EventKind",
    "Facts",
    "PrEvent",
    "PrSnapshot",
    "PrState",
    "Rejected",
    "Transition",
    "get_pr_snapshot",
    "get_switch_thread_id",
    "handle_event",
    "handle_webhook_delivery",
    "on_step_done",
    "on_step_done_in_transaction",
    "receive_webhook",
    "save_switch_thread_id",
]
