"""Public interface of the tenancy module.

Other modules may import only from this file.
"""

from heimdall.modules.tenancy._config_model import IntakeConfig, RepoConfig, ReviewConfig
from heimdall.modules.tenancy.domain import (
    InstallationAction,
    InstallationEvent,
    RepoSummary,
    parse_installation_event,
)
from heimdall.modules.tenancy.ports import ConfigFileReader
from heimdall.modules.tenancy.service import (
    get_repo_full_name_in_transaction,
    get_repo_id,
    get_repo_id_in_transaction,
    get_tenant,
    link_repo_room,
    load_config,
    load_config_in_transaction,
    on_installation_event,
    repo_auto_suggest_paused,
    repo_is_enabled,
    repo_switch_room_id,
    set_auto_suggest_paused,
    set_auto_suggest_paused_in_transaction,
)

__all__ = [
    "ConfigFileReader",
    "InstallationAction",
    "InstallationEvent",
    "IntakeConfig",
    "RepoConfig",
    "RepoSummary",
    "ReviewConfig",
    "get_repo_full_name_in_transaction",
    "get_repo_id",
    "get_repo_id_in_transaction",
    "get_tenant",
    "link_repo_room",
    "load_config",
    "load_config_in_transaction",
    "on_installation_event",
    "parse_installation_event",
    "repo_auto_suggest_paused",
    "repo_is_enabled",
    "repo_switch_room_id",
    "set_auto_suggest_paused",
    "set_auto_suggest_paused_in_transaction",
]
