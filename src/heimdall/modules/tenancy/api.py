"""Public interface of the tenancy module.

Other modules may import only from this file.
"""

from heimdall.modules.tenancy.domain import RepoConfig
from heimdall.modules.tenancy.service import TenancyDeps, load_config

__all__ = ["RepoConfig", "TenancyDeps", "load_config"]
