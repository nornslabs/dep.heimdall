# Heimdall

Heimdall triages open source pull requests with rule checks and three agents. It drafts
findings and suggested replies in a Mattermost room; a maintainer must approve the exact
draft before anything is posted to GitHub.

## Run the full local stack

Install Docker Compose, then prepare the local configuration and GitHub App key:

```bash
cp .env.example .env
# Set SMEE_URL to a channel created at https://smee.io/new.
# Fill in the GitHub App, Mattermost and model settings needed for your demo.
mkdir -p secrets
# Save the GitHub App private key as secrets/github-app.pem.
docker compose up --build
```

One `docker compose up` starts Postgres (with separate Heimdall and Mattermost databases),
Mattermost, the migration step, API, worker, MCP server, bridge, Huginn, Loki, Heimdall,
and the Smee webhook forwarder. The forwarded GitHub webhook target is
`http://api:8000/webhooks/github`; the API is available at `http://localhost:8000` and
Mattermost at `http://localhost:8065`.

Configuration is read from `.env`. The GitHub App private key is mounted read-only from
`secrets/github-app.pem` at `/run/secrets/github-app.pem`; it is not copied into the image.
The `secrets/` directory and `.env` are git-ignored. The Smee channel URL is required for
webhook forwarding; without it the tunnel container exits with a setup message.

## Development checks

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run pytest tests/unit
```
