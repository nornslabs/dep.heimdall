"""Public tenancy use cases collected from focused private helpers."""

from heimdall.modules.tenancy._config_load import load_config, load_config_in_transaction
from heimdall.modules.tenancy._installations import get_tenant, on_installation_event
from heimdall.modules.tenancy._repo_reference import get_repo_id, get_repo_id_in_transaction
from heimdall.modules.tenancy._repo_state import (
    repo_auto_suggest_paused,
    repo_is_enabled,
    repo_switch_room_id,
    set_auto_suggest_paused,
    set_auto_suggest_paused_in_transaction,
)

__all__ = [
    "get_repo_id",
    "get_repo_id_in_transaction",
    "get_tenant",
    "load_config",
    "load_config_in_transaction",
    "on_installation_event",
    "repo_auto_suggest_paused",
    "repo_is_enabled",
    "repo_switch_room_id",
    "set_auto_suggest_paused",
    "set_auto_suggest_paused_in_transaction",
]
