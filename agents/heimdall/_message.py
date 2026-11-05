"""The room message for a finished Heimdall run, rendered from its validated result."""

from agents.heimdall.schemas import DeskResult

EMPTY_EXPLANATION_MESSAGE = "Heimdall: I have nothing to add that is about the code."


def render_desk_result(result: DeskResult, saved_message: str | None = None) -> str:
    """Render the room message from a validated desk result.

    An explanation is the one case where the agent's own words reach the room, and they are
    the validated, length-capped field. The other actions point at work already saved by
    Heimdall's tenant-scoped tools, so the trusted text for that work (the approval message
    or the mechanical-task block) is passed in rather than taken from the model's reply.

    Args:
        result: The submission that passed schema validation and the id check.
        saved_message: Trusted text for the saved card, draft or task.

    Returns:
        The room message.

    Raises:
        ValueError: the action saves an artifact but no trusted message was passed.
    """
    if result.action == "explanation":
        if not result.explanation:
            return EMPTY_EXPLANATION_MESSAGE
        return f"Heimdall: {result.explanation}"
    if not saved_message:
        raise ValueError("saved_message is required unless the action is an explanation")
    return saved_message
