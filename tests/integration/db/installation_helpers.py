"""Small builders for installation events shared by database integration tests."""

from heimdall.modules.tenancy.api import InstallationAction, InstallationEvent, RepoSummary
from heimdall.shared.ids import InstallationId, RepoId


def created_installation(
    installation_id: int,
    account_login: str,
    repo_id: int,
    full_name: str,
) -> InstallationEvent:
    """Build a new installation event with one enabled repository."""
    return InstallationEvent(
        action=InstallationAction.UPSERT,
        installation_id=InstallationId(installation_id),
        account_login=account_login,
        account_type="Organization",
        repos_enabled=(RepoSummary(repo_id=RepoId(repo_id), full_name=full_name),),
    )
