"""Public interface of the tenancy module.

Other modules may import only from this file.
"""

from heimdall.modules.tenancy.domain import (
    InstallationEvent,
    RepoConfig,
    parse_installation_event,
)
from heimdall.modules.tenancy.service import (
    TenancyDeps,
    get_tenant,
    load_config,
    on_installation_event,
)

__all__ = [
    "InstallationEvent",
    "RepoConfig",
    "TenancyDeps",
    "get_tenant",
    "load_config",
    "on_installation_event",
    "parse_installation_event",
]
