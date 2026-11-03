import uuid

import pytest

from heimdall.modules.audit.api import UsageRecord


def test_usage_record_accepts_counts_and_optional_pr() -> None:
    pr_id = uuid.uuid4()

    record = UsageRecord(agent="loki", model="m-1", input_tokens=10, output_tokens=3, pr_id=pr_id)

    assert (record.agent, record.model, record.pr_id) == ("loki", "m-1", pr_id)


@pytest.mark.parametrize(
    ("agent", "model", "input_tokens", "output_tokens"),
    [
        ("", "m-1", 1, 1),
        ("loki", " ", 1, 1),
        ("loki", "m-1", -1, 1),
        ("loki", "m-1", 1, -5),
    ],
)
def test_usage_record_rejects_blank_names_and_negative_counts(
    agent: str, model: str, input_tokens: int, output_tokens: int
) -> None:
    with pytest.raises(ValueError):
        UsageRecord(agent, model, input_tokens, output_tokens)
