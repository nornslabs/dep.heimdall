"""Deliver outbox ``switch`` rows as the agent that wrote them, or as the bot if none did."""

from collections.abc import Mapping
from typing import Protocol, cast

from heimdall.adapters.db.outbox.models import OutboxMessage
from heimdall.adapters.db.outbox.sender import ChannelSender
from heimdall.shared.agents import AgentName
from heimdall.shared.errors import SwitchRequestFailed, SwitchUnavailable

AGENT_FIELD = "agent"
PR_FIELD = "pr_id"
THREAD_ROLE_FIELD = "thread_role"
ROOT_ROLE = "root"


class AgentPostPort(Protocol):
    """Posts room text as one agent; ``AgentPoster`` is the production implementation."""

    async def post(self, room_id: str, content: str) -> None:
        """Post ``content`` in ``room_id`` under this agent's identity."""
        ...

    async def post_in_thread(self, content: str, thread_id: str | None) -> str:
        """Post ``content`` as a thread root (``thread_id`` None) or reply; return its id."""
        ...

    async def post_rich(
        self,
        room_id: str,
        content: str,
        thread_id: str | None,
        *,
        attachments: list[dict[str, object]],
    ) -> str | None:
        """Post ``content`` with rich ``attachments`` as a thread root or reply; return its id.

        Returns None when the poster cannot render rich messages (no Mattermost bot), so the
        caller falls back to plain markdown.
        """
        ...


class ThreadStore(Protocol):
    """Where each PR's Switch thread id is kept, so every agent replies into the same thread."""

    async def get(self, installation_id: int, pr_id: str) -> str | None:
        """Return the PR's thread id, or None when its thread is not open yet."""
        ...

    async def save(self, installation_id: int, pr_id: str, thread_id: str) -> None:
        """Remember ``thread_id`` as the PR's thread."""
        ...


class SwitchRoutingSender:
    """Send a row through its agent's Switch key, or through the Mattermost bot.

    A row with no ``agent`` key keeps the old path, so existing writers are unaffected. A row
    that names an agent never falls back to the bot: a wrong or missing identity fails the row
    (it goes dead and the room is told) instead of posting under the wrong name. The bot is
    optional so agent posting works on a deployment that has agent keys but no Mattermost bot;
    a row that needs the missing bot fails the same way instead of waiting forever.
    """

    def __init__(
        self,
        bot: ChannelSender | None,
        posters: Mapping[AgentName, AgentPostPort],
        threads: ThreadStore | None = None,
    ) -> None:
        """Wire the fallback bot, one poster per configured agent and the PR thread store.

        Args:
            bot: Sender for rows that name no agent, or ``None`` when no bot is configured.
            posters: Poster for each agent whose Switch key is configured.
            threads: Where PR thread ids are kept. Without it every agent row posts flat.
        """
        self._bot = bot
        self._posters = posters
        self._threads = threads

    async def send(self, message: OutboxMessage) -> None:
        """Deliver ``message`` under the identity its payload names.

        Args:
            message: A ``switch`` outbox row.

        Raises:
            SwitchRequestFailed: the agent name is invalid or has no key, the row needs the bot
                and none is configured, or the payload has no room or text. Not retryable, and
                the text never repeats payload values.
            SwitchUnavailable: Switch could not be reached; retryable.
        """
        if AGENT_FIELD not in message.payload:
            await self._send_as_bot(message)
            return
        poster = self._poster_for(message.payload[AGENT_FIELD])
        room_id, content, attachments = _room_and_text(message)
        pr_id = _pr_id(message)
        if pr_id is None or self._threads is None:
            # A flat post cannot be threaded; rich formatting needs a thread, so post plain.
            await poster.post(room_id, content)
            return
        await self._post_in_pr_thread(message, poster, pr_id, content, attachments, room_id)

    async def _post_in_pr_thread(
        self,
        message: OutboxMessage,
        poster: AgentPostPort,
        pr_id: str,
        content: str,
        attachments: list[dict[str, object]] | None,
        room_id: str,
    ) -> None:
        """Post into the PR's thread, opening it first when this row is the root.

        A reply that arrives before the root has been delivered fails as unavailable, so the
        outbox tries it again shortly instead of posting it outside the thread. A root that
        finds its thread already open becomes a reply: a later push re-runs intake, and its
        checklist belongs in the same thread.

        When ``attachments`` were supplied the post is rendered as a rich Mattermost card;
        when the bot cannot render rich messages (no Mattermost bot configured) it falls back
        to the plain markdown message.
        """
        threads = self._threads
        if threads is None:
            raise SwitchRequestFailed("Switch thread store is not configured")
        thread_id = await threads.get(message.installation_id, pr_id)
        if thread_id is None and message.payload.get(THREAD_ROLE_FIELD) != ROOT_ROLE:
            raise SwitchUnavailable("the pull request's thread is not open yet")
        if attachments is not None:
            event_id = await poster.post_rich(room_id, content, thread_id, attachments=attachments)
            if event_id is None:
                # No Mattermost bot configured: the card cannot render, post markdown instead.
                event_id = await poster.post_in_thread(content, thread_id)
        else:
            event_id = await poster.post_in_thread(content, thread_id)
        if thread_id is None:
            await threads.save(message.installation_id, pr_id, event_id)

    async def _send_as_bot(self, message: OutboxMessage) -> None:
        """Send through the Mattermost bot, failing the row if there is none."""
        if self._bot is None:
            raise SwitchRequestFailed("no Mattermost bot is configured for this post")
        await self._bot.send(message)

    def _poster_for(self, raw_name: object) -> AgentPostPort:
        """Find the poster for the agent a payload names, or fail without echoing the value."""
        try:
            name = AgentName(raw_name)
        except ValueError:
            raise SwitchRequestFailed("Switch post names an unknown agent") from None
        poster = self._posters.get(name)
        if poster is None:
            raise SwitchRequestFailed(f"no Switch key is configured for agent {name.value}")
        return poster


def _room_and_text(
    message: OutboxMessage,
) -> tuple[str, str, list[dict[str, object]] | None]:
    """Return the room id, text and optional attachments of an agent post."""
    room_id = message.payload.get("channel_id")
    content = message.payload.get("message")
    if not isinstance(room_id, str) or not room_id:
        raise SwitchRequestFailed("Switch post payload has no channel_id")
    if not isinstance(content, str) or not content:
        raise SwitchRequestFailed("Switch post payload has no message")
    attachments = message.payload.get("attachments")
    if not isinstance(attachments, list):
        attachments = None
    else:
        attachments = cast(list[dict[str, object]], attachments)
    return room_id, content, attachments


def _pr_id(message: OutboxMessage) -> str | None:
    """Return the PR a post belongs to, or None for a post that is not part of a PR thread."""
    pr_id = message.payload.get(PR_FIELD)
    return pr_id if isinstance(pr_id, str) and pr_id else None
