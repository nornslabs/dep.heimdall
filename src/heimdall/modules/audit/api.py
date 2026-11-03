"""Public interface of the audit module.

Other modules may import only from this file.
"""

from heimdall.modules.audit.domain import UsageRecord
from heimdall.modules.audit.service import record_usage

__all__ = ["UsageRecord", "record_usage"]
