from __future__ import annotations

import json

import httpx
import pytest

from textql_agent.reader import (
    MAX_CHARTS,
    TextQLError,
    TextQLReader,
    chart_option,
    chart_title,
    connector_ids,
)

# The v2 ChatResponse shape from TextQL's published OpenAPI spec.
OK = {"response": "There were 1,204 rides on 2024-05-01.", "chat_id": "c-1", "id": "m-1"}

ASSET_URL = "https://textqlusercontent.com/asset/proxy/a1/chart.html?iat=1&signature=s"

CHART_HTML = (
    "<html><body><div id='x'></div><script>\n"
    "var chart_x = echarts.init(document.getElementById('x'));\n"
    'var option_310635add4724edaac88ca8c3fefc156 = {"title": [{"text": "Trips per Month"}], '
    '"xAxis": [{"data": ["2013-07", "2013-08"]}], "series": [{"type": "line", "data": [1, 2]}]};\n'
    "chart_x.setOption(option_310635add4724edaac88ca8c3fefc156);\n"
    "</script></body></html>"
)


def chat_with(*assets: dict) -> dict:
    return {"chat": {"id": "c-1"}, "messages": [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "Chart looks clean.", "assets": list(assets)},
    ], "assets": list(assets)}


def chart_asset(url: str = ASSET_URL, name: str = "echarts_chart_1_trips.html") -> dict:
    return {"type": "chart", "name": name, "url": url}


def make_reader(handler, **kwargs) -> TextQLReader:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return TextQLReader("tql_test", client=client, **kwargs)


def serving(chat: dict, pages: dict[str, httpx.Response] | None = None, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.method == "POST":
            return httpx.Response(200, json=OK)
        if request.url.path == "/v2/chats/c-1":
            return httpx.Response(200, json=chat)
        return (pages or {}).get(str(request.url), httpx.Response(200, text=CHART_HTML))
    return handler


def test_ask_posts_the_question_with_the_bearer_key_and_returns_the_answer():
    seen: list[httpx.Request] = []
    answer = make_reader(serving(chat_with(), seen=seen)).ask_detailed("rides on may 1")

    assert answer.text == OK["response"]
    post = seen[0]
    assert str(post.url) == "https://app.textql.com/v2/chats"
    assert post.headers["authorization"] == "Bearer tql_test"
    assert json.loads(post.content) == {
        "question": "rides on may 1",
        "tools": {"sql_enabled": True, "python_enabled": True},
    }


def test_configured_connectors_are_sent_so_ana_uses_only_those():
    seen: list[httpx.Request] = []
    make_reader(serving(chat_with(), seen=seen), connector_ids=(70, 630)).ask_detailed("q")
    assert json.loads(seen[0].content)["tools"]["connector_ids"] == [70, 630]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"error": {"message": "bad key"}}),
        httpx.Response(200, text="<html>gateway</html>"),
        httpx.Response(200, json={"response": "  "}),
    ],
    ids=["refused", "not-json", "empty"],
)
def test_an_unusable_reply_raises_rather_than_becoming_an_answer(response):
    with pytest.raises(TextQLError):
        make_reader(lambda r: response).ask_detailed("q")


def test_connector_ids_are_read_from_a_comma_list_and_junk_is_ignored():
    assert connector_ids("70, 630,,abc") == (70, 630)
    assert connector_ids(None) == ()


def test_a_chart_ana_drew_comes_back_with_its_title_and_settings():
    answer = make_reader(serving(chat_with(chart_asset()))).ask_detailed("chart it")
    (chart,) = answer.charts
    assert chart["title"] == "Trips per Month"
    assert chart["option"]["series"][0]["data"] == [1, 2]


def test_the_chart_download_does_not_carry_the_textql_key():
    seen: list[httpx.Request] = []
    make_reader(serving(chat_with(chart_asset()), seen=seen)).ask_detailed("q")
    (download,) = [r for r in seen if r.url.host == "textqlusercontent.com"]
    assert "authorization" not in download.headers


def test_a_chart_that_cannot_be_downloaded_is_skipped_and_the_answer_survives():
    pages = {ASSET_URL: httpx.Response(403, text="signature expired")}
    answer = make_reader(serving(chat_with(chart_asset()), pages)).ask_detailed("q")
    assert answer.text == OK["response"]
    assert answer.charts == ()


def test_the_answer_survives_when_the_chat_itself_cannot_be_read():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=OK)
        return httpx.Response(500, text="boom")

    answer = make_reader(handler).ask_detailed("q")
    assert answer.text == OK["response"]
    assert answer.charts == ()


def test_a_file_with_no_chart_settings_in_it_is_skipped():
    pages = {ASSET_URL: httpx.Response(200, text="<html>no chart here</html>")}
    assert make_reader(serving(chat_with(chart_asset()), pages)).ask_detailed("q").charts == ()


def test_assets_that_are_not_chart_pages_are_ignored():
    table = {"type": "table", "name": "rows.csv", "url": "https://textqlusercontent.com/t"}
    meta = chart_asset(name="echarts_chart_1_trips.meta.json")
    assert make_reader(serving(chat_with(table, meta))).ask_detailed("q").charts == ()


def test_no_more_than_the_chart_limit_is_collected():
    many = [chart_asset(url=f"{ASSET_URL}&n={i}") for i in range(MAX_CHARTS + 2)]
    assert len(make_reader(serving(chat_with(*many))).ask_detailed("q").charts) == MAX_CHARTS


def test_chart_option_reads_the_settings_and_rejects_anything_else():
    assert chart_option(CHART_HTML)["title"] == [{"text": "Trips per Month"}]
    assert chart_option("var option_ab = {not json};") is None
    assert chart_option("<html></html>") is None


def test_chart_title_falls_back_to_the_file_name():
    assert chart_title({"title": {"text": " Revenue "}}, "f.html") == "Revenue"
    assert chart_title({"title": []}, "f.html") == "f.html"
    assert chart_title({}, "f.html") == "f.html"


@pytest.mark.parametrize(
    "chat",
    [[1, 2], {"messages": "x"}, {"messages": [{"assets": ["a string"]}]},
     {"messages": ["not a message"], "assets": 7}],
    ids=["list", "messages-a-string", "asset-a-string", "message-a-string"],
)
def test_a_chat_in_an_unexpected_shape_loses_the_charts_not_the_answer(chat):
    answer = make_reader(serving(chat)).ask_detailed("q")
    assert answer.text == OK["response"]
    assert answer.charts == ()
