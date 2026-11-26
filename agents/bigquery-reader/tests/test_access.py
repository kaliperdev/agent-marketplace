"""A call that carries the router's signed access note reads with its own pass, datasets and table map."""

import base64
import hashlib
import hmac
import json

import httpx
import pytest

from agent_kit import auth
from bigquery_reader_agent import access
from bigquery_reader_agent.bigquery import BigQuery, BigQueryError, DatasetRef, TableRef
from bigquery_reader_agent.server import build_tools
from bigquery_reader_agent.tablemap import MapCaches
from bigquery_reader_agent.tools import make_bq_tools
from bq_fake import FakeBigQuery
from notes import AT, NOTE, SECRET, payload, sign

PROJECT = "rytangle-demo"
OWN = (DatasetRef(PROJECT, "marketing_test"),)
SCHEMA = [{"name": "n", "type": "INTEGER", "mode": "NULLABLE"}]
GB = 1_000_000_000


def tools(fake, clock=lambda: AT):
    client = httpx.Client(transport=httpx.MockTransport(fake.handler))
    per_call = access.per_call(SECRET, client, MapCaches(), clock=clock)
    return {t.name: t for t in make_bq_tools(BigQuery(client, lambda: "own-token", PROJECT), OWN, GB, per_call=per_call)}


def run(tool, *args, note=NOTE, token="pass-1"):
    with auth.serving(f"Bearer {token}" if token else None, note):
        return tool.func(*args)


def test_the_routers_note_is_read():
    note = access.read_note(NOTE, SECRET, now=AT + 30)
    assert (note.agent, note.login, note.project) == ("finance", "3", "finance-1234")
    assert note.datasets == (DatasetRef("finance-1234", "ledger"), DatasetRef("finance-1234", "payroll_summary"))
    assert note.key == ("3", "finance-1234", ("finance-1234.ledger", "finance-1234.payroll_summary"))


WIDENED = sign(payload(datasets=["finance-1234.ledger", f"{PROJECT}.marketing_test"]), SECRET).split(".")[0] + "." + NOTE.split(".")[1]


@pytest.mark.parametrize("raw, secret, now, why", [
    (WIDENED, SECRET, AT, "not signed by this bot's router"),
    (NOTE, "another-bots-key", AT, "not signed by this bot's router"),
    (NOTE, SECRET, AT + 61, "not fresh"), (NOTE, SECRET, AT - 61, "not fresh"),
    ("no-stamp-here", SECRET, AT, "not readable"),
    (sign(payload(datasets=[]), SECRET), SECRET, AT, "not readable"),
    (sign(payload(v=2), SECRET), SECRET, AT, "not readable"),
    (sign(payload(datasets=[f"p.d{i}" for i in range(101)]), SECRET), SECRET, AT, "not readable"),
    (NOTE, None, AT, "started without ACCESS_NOTE_KEY"),
])
def test_a_note_that_cannot_be_trusted_is_refused(raw, secret, now, why):
    with pytest.raises(access.AccessRefused, match=why) as err:
        access.read_note(raw, secret, now)
    assert NOTE.split(".")[1] not in str(err.value)


def test_a_signed_note_that_is_not_json_is_not_readable():
    body = "bm90IGpzb24"  # "not json"
    forged = f"{body}.{sign(payload(), SECRET).split('.')[1]}"
    with pytest.raises(access.AccessRefused, match="not signed"):
        access.read_note(forged, SECRET, AT)
    stamp = base64.urlsafe_b64encode(hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    with pytest.raises(access.AccessRefused, match="not readable"):
        access.read_note(f"{body}.{stamp}", SECRET, AT)


def test_a_call_with_a_note_reads_only_the_notes_datasets_with_the_notes_pass():
    fake = FakeBigQuery()
    fake.add_table("finance-1234.ledger.entries", SCHEMA)
    fake.add_table(f"{PROJECT}.marketing_test.stats", SCHEMA)
    t = tools(fake)
    text, _ = run(t["bq_describe_table"], f"{PROJECT}.marketing_test.stats")  # the reader's own list is never merged in
    assert "not in a dataset this agent may read (finance-1234.ledger, finance-1234.payroll_summary)" in text
    assert fake.requests == []
    run(t["bq_describe_table"], "ledger.entries")                             # dataset.table: the note's project
    assert fake.requests[-1].url.path.endswith("/projects/finance-1234/datasets/ledger/tables/entries")
    assert fake.requests[-1].headers["authorization"] == "Bearer pass-1"


def test_a_query_joining_an_allowed_dataset_with_another_is_refused_before_it_runs():
    fake = FakeBigQuery()
    sql = "select * from ledger.entries join marketing_test.stats using (n)"
    fake.dry_runs[sql] = {"statementType": "SELECT", "bytes": 10,
                          "tables": ["finance-1234.ledger.entries", f"{PROJECT}.marketing_test.stats"]}
    text, _ = run(tools(fake)["bq_run_query"], sql)
    assert text.startswith(f"Refused: {PROJECT}.marketing_test.stats is not in a dataset this agent may read")
    assert [r.url.path for r in fake.requests] == ["/bigquery/v2/projects/finance-1234/jobs"]  # the dry run only


def test_a_query_over_a_dataset_the_note_allows_runs_with_the_notes_pass():
    fake = FakeBigQuery()
    sql = "select n from ledger.entries"
    fake.dry_runs[sql] = {"statementType": "SELECT", "bytes": 10, "tables": ["finance-1234.ledger.entries"]}
    fake.results[sql] = {"schema": SCHEMA, "rows": [{"f": [{"v": "7"}]}], "bytes": 10}
    text, _ = run(tools(fake)["bq_run_query"], sql)
    assert text.startswith("1 row") and "7" in text
    assert {r.headers["authorization"] for r in fake.requests} == {"Bearer pass-1"}


def test_information_schema_is_refused_with_a_note_too():
    fake = FakeBigQuery()
    text, _ = run(tools(fake)["bq_run_query"], "select * from ledger.INFORMATION_SCHEMA.TABLES")
    assert text.startswith("Refused: INFORMATION_SCHEMA is not read here")
    assert fake.requests == []


def test_a_call_without_a_note_reads_as_today():
    fake = FakeBigQuery()
    fake.add_table(f"{PROJECT}.marketing_test.stats", SCHEMA)
    run(tools(fake)["bq_describe_table"], "marketing_test.stats", note=None, token=None)
    assert fake.requests[-1].headers["authorization"] == "Bearer own-token"


def test_a_note_without_a_pass_never_falls_back_to_the_readers_own_login():
    fake = FakeBigQuery()
    with pytest.raises(access.AccessRefused, match="came without its Google pass"):
        run(tools(fake)["bq_list_tables"], token=None)
    assert fake.requests == []


def test_a_stale_note_is_refused_and_nothing_is_read():
    fake = FakeBigQuery()
    with pytest.raises(access.AccessRefused, match="not fresh"):
        run(tools(fake, clock=lambda: AT + 120)["bq_describe_table"], "ledger.entries")
    assert fake.requests == []


def test_a_reader_without_a_note_key_refuses_a_note_rather_than_reading_with_its_own_login():
    fake = FakeBigQuery()
    client = httpx.Client(transport=httpx.MockTransport(fake.handler))
    per_call = access.per_call(None, client, MapCaches(), clock=lambda: AT)
    own = BigQuery(client, lambda: "own-token", PROJECT)
    t = {x.name: x for x in make_bq_tools(own, OWN, GB, per_call=per_call)}
    with pytest.raises(access.AccessRefused, match="started without ACCESS_NOTE_KEY"):
        run(t["bq_list_tables"])
    assert fake.requests == []


def test_each_access_is_served_its_own_map_and_never_anothers():
    fake = FakeBigQuery()
    fake.add_table(f"{PROJECT}.marketing_test.stats", SCHEMA)
    fake.add_table("finance-1234.ledger.entries", SCHEMA)
    fake.add_table("finance-1234.payroll_summary.pay", SCHEMA)
    a = sign(payload(datasets=["finance-1234.ledger"]), SECRET)
    b = sign(payload(datasets=["finance-1234.payroll_summary"]), SECRET)
    t = tools(fake)
    run(t["bq_list_tables"], note=None, token=None)                       # the reader's own map, built first
    text_a, text_b = run(t["bq_list_tables"], note=a)[0], run(t["bq_list_tables"], note=b)[0]
    assert "ledger.entries" in text_a and "payroll_summary" not in text_a and "marketing_test" not in text_a
    assert "payroll_summary.pay" in text_b and "ledger" not in text_b
    listed = sum(r.url.path.endswith("/tables") for r in fake.requests)
    run(t["bq_list_tables"], True, note=b)                                # B rebuilds its own map only
    assert run(t["bq_list_tables"], note=a)[0] == text_a
    assert sum(r.url.path.endswith("/tables") for r in fake.requests) == listed + 1


def test_a_map_is_built_with_the_notes_pass_even_on_worker_threads():
    fake = FakeBigQuery()
    fake.add_table("finance-1234.ledger.entries", SCHEMA)
    fake.add_table("finance-1234.ledger.more", SCHEMA)
    run(tools(fake)["bq_list_tables"])
    assert len(fake.requests) > 2
    assert {r.headers["authorization"] for r in fake.requests} == {"Bearer pass-1"}


def test_a_note_for_another_login_or_project_is_another_access():
    a = access.read_note(NOTE, SECRET, AT)
    other_login = access.read_note(sign(payload(login="4"), SECRET), SECRET, AT)
    other_project = access.read_note(sign(payload(project="other-9", datasets=["ledger"]), SECRET), SECRET, AT)
    assert len({a.key, other_login.key, other_project.key}) == 3
    reordered = access.read_note(sign(payload(datasets=list(reversed(payload()["datasets"]))), SECRET), SECRET, AT)
    assert reordered.key == a.key


def test_build_tools_checks_notes_with_the_key_it_is_given(tmp_path):
    env = {"GOOGLE_SA_JSON": "/nowhere.json", "BQ_PROJECT": PROJECT, "BQ_DATASETS": "marketing_test",
           "ACCESS_NOTE_KEY": f"  {SECRET}\n"}
    fake = FakeBigQuery()
    tools_, problem = build_tools(env, httpx.Client(transport=httpx.MockTransport(fake.handler)), tmp_path)
    assert problem is None
    describe = next(t for t in tools_ if t.name == "bq_describe_table")
    fake.add_table("finance-1234.ledger.entries", SCHEMA)
    with auth.serving("Bearer pass-1", NOTE):   # the clock is real here: this note is long stale
        with pytest.raises(access.AccessRefused, match="not fresh"):
            describe.func("ledger.entries")
    with auth.serving(None, None):               # no note: the reader's own datasets, as in 1.0.0
        text, _ = describe.func("finance-1234.ledger.entries")
    assert "marketing_test" in text


VECTOR = ("eyJhZ2VudCI6ImZpbmFuY2UiLCJhdCI6MTc2NDExNTIwMCwiZGF0YXNldHMiOlsiZmluYW5jZS0xMjM0LmxlZGdlciIsImZpbmFuY2UtMTIzNC5wYXlyb2xs"
          "X3N1bW1hcnkiXSwibG9naW4iOiIzIiwicHJvamVjdCI6ImZpbmFuY2UtMTIzNCIsInYiOjF9.vpa-4t3DvylOpHBLny7JHOuj1-LoAIMKWs-ORrRcDK4")


def test_the_helper_writes_the_contracts_test_vector_exactly():
    assert NOTE == VECTOR
    assert access.read_note(VECTOR, SECRET, AT).project == "finance-1234"


@pytest.mark.parametrize("at", ["NaN", "Infinity", "-Infinity"])
def test_a_signed_at_that_is_not_a_finite_number_is_not_readable(at):
    body = json.dumps(payload()).replace(str(AT), at)
    assert at in body
    text = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    stamp = base64.urlsafe_b64encode(hmac.new(SECRET.encode(), text.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    with pytest.raises(access.AccessRefused, match="not readable"):
        access.read_note(f"{text}.{stamp}", SECRET, AT)


def test_a_pass_without_a_note_still_reads_with_the_readers_own_login():
    fake = FakeBigQuery()
    fake.add_table(f"{PROJECT}.marketing_test.stats", SCHEMA)
    run(tools(fake)["bq_describe_table"], "marketing_test.stats", note=None, token="pass-1")
    assert fake.requests[-1].headers["authorization"] == "Bearer own-token"


@pytest.mark.parametrize("name", [
    "ledger.x/%2E%2E/%2E%2E/payroll/tables/salaries",
    "ledger.x?alias=1", "ledger.x#frag", "ledger.x\\y", "ledger.x`y", "ledger.x\ny", "finance-1234.ledger.a%2Fb", "fin/ance.ledger.entries",
    "finance-1234.led/ger.entries"])
@pytest.mark.parametrize("tool_name", ["bq_describe_table", "bq_preview_table"])
def test_a_name_with_characters_no_table_name_has_is_refused_before_any_request(name, tool_name):
    fake = FakeBigQuery()
    text, _ = run(tools(fake)[tool_name], name)
    assert text == "That table name has characters a BigQuery table name never has."
    assert fake.requests == []


def many_tables(sql, tables):
    fake = FakeBigQuery()
    fake.dry_runs[sql] = {"statementType": "SELECT", "bytes": 10, "tables": tables}
    fake.results[sql] = {"schema": SCHEMA, "rows": [{"f": [{"v": "7"}]}], "bytes": 10}
    return fake


def test_a_note_call_whose_dry_run_lists_60_allowed_tables_runs():
    sql = "select n from ledger.t_*"
    fake = many_tables(sql, [f"finance-1234.ledger.t{i}" for i in range(60)])
    assert run(tools(fake)["bq_run_query"], sql)[0].startswith("1 row")


def test_a_note_call_listing_60_tables_with_one_outside_the_list_is_refused_by_the_dataset_check():
    sql = "select n from ledger.t_*"
    fake = many_tables(sql, [f"finance-1234.ledger.t{i}" for i in range(59)] + [f"{PROJECT}.marketing_test.stats"])
    text, _ = run(tools(fake)["bq_run_query"], sql)
    assert text.startswith(f"Refused: {PROJECT}.marketing_test.stats is not in a dataset this agent may read")
    assert not any(r.url.path.endswith("/queries") for r in fake.requests)


@pytest.mark.parametrize("sql", ["select * from external_query('c', 'select 1')", "SELECT * FROM EXTERNAL_QUERY(\"c\", \"x\")",
                                 "select * from External_Query('c','x')"])
def test_a_call_with_a_note_may_not_query_through_an_external_connection(sql):
    fake = many_tables(sql, ["finance-1234.ledger.entries"])
    text, _ = run(tools(fake)["bq_run_query"], sql)
    assert text == "This query reads through an external connection, which this agent may not use."
    assert fake.requests == []


def test_a_call_without_a_note_may_still_name_external_query():
    sql = "select * from external_query('c', 'select 1')"
    fake = many_tables(sql, [f"{PROJECT}.marketing_test.stats"])
    assert run(tools(fake)["bq_run_query"], sql, note=None, token=None)[0].startswith("1 row")


def test_table_names_are_quoted_segment_by_segment_in_the_request_path():
    fake = FakeBigQuery()
    fake.add_table("p.d.we ird$1", SCHEMA)
    bq = BigQuery(httpx.Client(transport=httpx.MockTransport(fake.handler)), lambda: "t", "p")
    try:
        bq.table(TableRef("p", "d", "x/../../other/tables/y"))
    except Exception:
        pass
    try:
        bq.tables(DatasetRef("p", "a/b"))
    except Exception:
        pass
    raw = [r.url.raw_path.decode().split("?")[0] for r in fake.requests]
    assert raw == ["/bigquery/v2/projects/p/datasets/d/tables/x%2F..%2F..%2Fother%2Ftables%2Fy",
                   "/bigquery/v2/projects/p/datasets/a%2Fb/tables"]


@pytest.mark.parametrize("name", ["ledger.table 01", "ledger.tabl\u00e9-2020", "ledger.events_*", "ledger.t$20200101",
                                  "ledger.caf\u0065\u0301", "ledger.\u30c6\u30fc\u30d6\u30eb"])
def test_a_valid_table_name_is_not_refused(name):
    fake = FakeBigQuery()
    fake.add_table(f"finance-1234.{name}", SCHEMA)
    text, _ = run(tools(fake)["bq_describe_table"], name)
    assert text != "That table name has characters a BigQuery table name never has."
    assert fake.requests


@pytest.mark.parametrize("part", ["", ".", "..", "..."])
def test_a_path_segment_that_is_empty_or_only_dots_is_refused_before_a_request(part):
    fake = FakeBigQuery()
    bq = BigQuery(httpx.Client(transport=httpx.MockTransport(fake.handler)), lambda: "t", "p")
    with pytest.raises(BigQueryError):
        bq.table(TableRef("p", "d", part))
    with pytest.raises(BigQueryError):
        bq.tables(DatasetRef("p", part))
    assert fake.requests == []


@pytest.mark.parametrize("sql", ["select * from `EXTERNAL\\x5fQUERY`('c','x')", "select 1 from `a\\b.c`", "select * from x.`t\\u0041`"])
def test_a_call_with_a_note_may_not_use_an_escaped_name(sql):
    fake = many_tables(sql, ["finance-1234.ledger.entries"])
    text, _ = run(tools(fake)["bq_run_query"], sql)
    assert text == "This query uses an escaped name, which this agent may not use."
    assert fake.requests == []


def test_a_call_without_a_note_may_use_a_backslash_in_a_quoted_name():
    sql = "select 1 from `a\\b.c`"
    fake = many_tables(sql, [f"{PROJECT}.marketing_test.stats"])
    assert run(tools(fake)["bq_run_query"], sql, note=None, token=None)[0].startswith("1 row")


def test_a_backslash_in_a_string_literal_is_not_an_escaped_name():
    sql = "select n from ledger.entries where s = 'a\\nb'"
    fake = many_tables(sql, ["finance-1234.ledger.entries"])
    assert run(tools(fake)["bq_run_query"], sql)[0].startswith("1 row")


ESCAPED = "This query uses an escaped name, which this agent may not use."
UNCLOSED = "This query could not be checked: a quote or comment is not closed."
EXTERNAL = "This query reads through an external connection, which this agent may not use."


def asked(sql):
    fake = many_tables(sql, ["finance-1234.ledger.entries"])
    return run(tools(fake)["bq_run_query"], sql)[0]


@pytest.mark.parametrize("sql, said", [
    (r"select '`' as a, `EXTERNAL\x5fQUERY`('c','x')", ESCAPED),          # a stray backtick in a string
    ("-- a` note\nselect 1 from `a\\b.c`", ESCAPED),                       # ... in a comment
    (r'select "`" as a, `a\b`.c from x', ESCAPED),                        # ... in "..."
    (r"select /* ` */ 1 from `a\b.c`", ESCAPED),                          # ... in /* */
    ("select 1 # `\nfrom `a\\b.c`", ESCAPED),                             # ... in a # comment
    ("select '''` \\' \n''' as a from `a\\b.c`", ESCAPED),                # ... in a triple-quoted string
    (r"select r'`' as a from `a\b.c`", ESCAPED),                          # ... in a raw string
    (r"select 1 from `a\`b`", ESCAPED),                                   # an escaped backtick in a name
    ("select 'x", UNCLOSED), ('select "x', UNCLOSED), ("select '''x'' ", UNCLOSED), ('select """x""', UNCLOSED),
    ("select 1 /* x", UNCLOSED), ("select 1 from `a", UNCLOSED), ("select r'x", UNCLOSED), ("select rb'x", UNCLOSED),
    (r"select r'a\' from x", UNCLOSED),                                   # a raw string's backslash before its end quote: not checkable
    ("select 1 from `external_query`", EXTERNAL),
    ("select * from external_/**/query('c','x')", EXTERNAL),
])
def test_the_scanner_refuses_what_it_cannot_trust_on_a_call_with_a_note(sql, said):
    assert asked(sql) == said


@pytest.mark.parametrize("sql", [
    r"select 'it`s' as a, 'b\nc' as b, `x` from ledger.t",                # the false refusal
    r"select 'it\'s' as a from `ledger`.t",                               # an escaped quote in a string
    r"""select r'\d+' as a, R"\w" as b, rb'\x', BR'\y' from ledger.t""",  # raw strings with backslashes
    "select '''a ` \\ b''' as a, \"\"\"c ` \\ \"\"\" as b from ledger.t",  # triple-quoted, with backticks
    "select 1 -- it's `a\\b\nfrom ledger.t",                              # a comment's quote and backslash
    "select 1 /* it's \\ ` */ from ledger.t",
    "select 1 # a \" \nfrom ledger.t",
    "select '' as a, \"\" as b from ledger.t",
    r"select 1 from `finance-1234.ledger.entries` where REGEXP_CONTAINS(x, r'\d+') and y = 'a\\b'",
    "select 1 from ledger.t -- trailing comment with no newline",
])
def test_the_scanner_lets_ordinary_queries_through_on_a_call_with_a_note(sql):
    assert asked(sql).startswith("1 row")


def test_the_scanner_is_not_applied_to_a_call_without_a_note():
    sql = "select 'x"
    fake = many_tables(sql, [f"{PROJECT}.marketing_test.stats"])
    assert run(tools(fake)["bq_run_query"], sql, note=None, token=None)[0].startswith("1 row")
