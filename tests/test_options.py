import pytest

from agentdown.core.options import load_options, parse_options


def test_bundled_options_load() -> None:
    opts = load_options()
    assert opts.version >= 2
    assert "other" in opts.what_failed_values
    assert all(o.description for o in opts.what_failed)  # D58: every option is described
    assert "notworking_canary" in opts.agent_type  # D51
    assert set(opts.target_type) == {"site", "route", "mcp", "skill"}


VALID = """
version: 1
what_failed:
  - {value: a, description: An a.}
agent_type: [b]
target_type: [site]
"""


def test_parse_valid() -> None:
    assert parse_options(VALID).what_failed_values == ("a",)


@pytest.mark.parametrize(
    "text",
    [
        "- just a list",
        VALID.replace("version: 1", "version: 0"),
        VALID.replace("  - {value: a, description: An a.}", "  - a"),
        VALID.replace("description: An a.", "description: ''"),
        VALID.replace("description: An a.", "description: " + "x" * 201),
        VALID.replace(
            "  - {value: a, description: An a.}",
            "  - {value: a, description: x}\n  - {value: a, description: y}",
        ),
        VALID.replace("agent_type: [b]", "agent_type: [1]"),
        VALID.replace("target_type: [site]\n", ""),
    ],
)
def test_parse_rejects_invalid(text: str) -> None:
    with pytest.raises(ValueError):
        parse_options(text)
