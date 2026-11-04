"""Provision Switch agents: register them, set who may address them, prepare Mattermost."""

from heimdall.adapters.switch.provisioning.addressing import SwitchAdmin, triage_room_policy
from heimdall.adapters.switch.provisioning.mattermost_admin import MattermostAdmin
from heimdall.adapters.switch.provisioning.registrar import AgentRegistrar, AgentRegistration
from heimdall.adapters.switch.provisioning.specs import AGENT_SPECS, AgentSpec

__all__ = [
    "AGENT_SPECS",
    "AgentRegistrar",
    "AgentRegistration",
    "AgentSpec",
    "MattermostAdmin",
    "SwitchAdmin",
    "triage_room_policy",
]
