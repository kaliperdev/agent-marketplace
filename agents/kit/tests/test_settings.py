from agent_kit.settings import credential, missing


def test_a_placeholder_or_blank_counts_as_missing():
    env = {"A": " tok ", "B": "xoxb-REPLACE-ME", "C": "  "}
    assert credential(env, "A") == "tok"
    assert credential(env, "B") is None
    assert credential(env, "C") is None
    assert credential(env, "D") is None


def test_missing_names_what_is_not_set_without_showing_values():
    assert missing({"A": "secret-value"}, ["A", "B", "C"]) == "B, C are not set"
    assert missing({"A": "x"}, ["A"]) is None
