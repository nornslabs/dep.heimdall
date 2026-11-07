"""Tone filter: drop sentences about the contributor as a person or how the code was written.

Agents describe code, never people. A sentence goes when it guesses how the code was produced
("looks AI-generated", "copy-pasted"), or when it names a person and passes judgement on them
("the author clearly didn't test this"). Naming a person alone is fine ("if you call `close()`
twice, it raises"), and code spans are ignored, so a field called `author` never trips it.
"""

import re

from heimdall.shared.logging import get_logger

_SENTENCE_BREAK = re.compile(r"((?<=[.!?])[ \t]+|\n+)")
_CODE_SPAN = re.compile(r"`[^`]*`")

log = get_logger(__name__)

_PROVENANCE_GUESS = re.compile(
    r"\b(ai|llm|gpt|chatgpt|copilot|machine)[- ]generated\b"
    r"|\b(written|generated|produced)\s+by\s+(an?\s+)?(ai|llm|gpt|chatgpt|copilot|bot)\b"
    r"|\bcopy[- ]?past(e|ed|ing)\b"
    r"|\bcopied\s+(from|off)\b"
    r"|\bvibe[- ]?coded\b",
    re.IGNORECASE,
)
_PERSON = re.compile(
    r"\b(author|contributor|developer|dev|whoever\s+wrote|you|your|yourself)\b",
    re.IGNORECASE,
)
_JUDGEMENT = re.compile(
    r"\b(clearly|obviously|forgot|forget|didn'?t\s+(test|check|bother|read)"
    r"|did\s+not\s+(test|check|bother|read|understand|know)"
    r"|sloppy|lazy|careless|rushed|inexperienced|junior|beginner|incompetent"
    r"|understand|seems|should\s+have\s+known)\b",
    re.IGNORECASE,
)


def filter_tone(text: str) -> str:
    """Remove every sentence about the contributor or about how the code was written.

    Args:
        text: Prose an agent wrote, such as a finding or a draft comment.

    Returns:
        The kept sentences with the breaks that followed them, trimmed; empty when nothing
        about the code was left.
    """
    parts = _SENTENCE_BREAK.split(text)
    kept: list[str] = []
    removed: list[str] = []
    for index in range(0, len(parts), 2):
        sentence = parts[index]
        if _is_about_the_person(sentence):
            removed.append(sentence)
            continue
        following = parts[index + 1] if index + 1 < len(parts) else ""
        kept.append(sentence + following)
    result = "".join(kept).strip()
    if removed:
        log.debug(
            "tone_filter_removed_sentences",
            removed=removed,
            kept=result,
        )
    return result


def _is_about_the_person(sentence: str) -> bool:
    """Say whether a sentence guesses at provenance or judges a named person."""
    prose = _CODE_SPAN.sub(" ", sentence)
    if _PROVENANCE_GUESS.search(prose):
        return True
    return bool(_PERSON.search(prose) and _JUDGEMENT.search(prose))
