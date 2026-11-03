"""Public interface of the tenancy module.

Other modules may import only from this file.
"""

from heimdall.modules.tenancy._config_model import IntakeConfig, RepoConfig, ReviewConfig
from heimdall.modules.tenancy.domain import (
    InstallationEvent,
    InstallationRepo,
    parse_installation_event,
)
from heimdall.modules.tenancy.ports import ConfigFileReader
from heimdall.modules.tenancy.service import (
    get_repo_id,
    get_repo_id_in_transaction,
    get_tenant,
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
    "InstallationEvent",
    "InstallationRepo",
    "IntakeConfig",
    "RepoConfig",
    "ReviewConfig",
    "get_repo_id",
    "get_repo_id_in_transaction",
    "get_tenant",
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
