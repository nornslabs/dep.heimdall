# Switch bridge

The bridge streams room events and records maintainer approvals from thumbs-up reactions on
draft posts. Approval posts include a hidden marker with the draft id, exact body hash,
installation id and repository. Mattermost usernames are treated as GitHub logins; deployments
must configure those identities consistently with `heimdall.yml` maintainer entries.

Non-maintainers receive one short thread reply. They create no approval or GitHub outbox entry.
