"""A call that carries the router's signed access note reads with its own pass, datasets and table map."""

import base64
import hashlib
import hmac

import httpx
import pytest

from agent_kit import auth
from bigquery_reader_agent import access
from bigquery_reader_agent.bigquery import BigQuery, DatasetRef
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
