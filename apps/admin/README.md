# admin

Operator command to point one repository at the Switch room its triage is posted to. It sets the
Mattermost channel id the bot posts to and, optionally, Switch's own room UUID where the agents
post (the two ids are not interchangeable — see `docs/switch.md`).

Run:

```bash
python -m apps.admin.link_room <installation_id> <repo_id> <room_id> --agent-room-id <uuid>
```

It requires `HEIMDALL_DATABASE_URL` (or the compose Postgres) and a linked installation, which it
loads through `tenancy.get_tenant`. `link_repo_room` only fills `switch_room_id` where the repo
has none, so a room an operator chose is never replaced.

|| File | Role |
|---|---|---|
|| `link_room.py` | CLI: parse ids, load tenant, call `tenancy.link_repo_room` |
