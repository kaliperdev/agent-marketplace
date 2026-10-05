from agent_kit.rows import nothing_found, no_rows


def test_nothing_found_says_what_was_searched_in_the_routers_words():
    # The router's "searched, found nothing" note and its one retry listen for
    # exactly these words (rytangle router/app/evaluate.py BARREN).
    assert nothing_found("q3 roadmap") == ("no matches for: 'q3 roadmap'", no_rows())
    assert nothing_found("q3 roadmap", "the shared drive")[0] == "no matches for: 'q3 roadmap' in the shared drive"
