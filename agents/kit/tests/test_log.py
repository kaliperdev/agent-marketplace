import re

from agent_kit.log import log, redact

LINE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z (INFO|WARN|ERROR) \[svc\] evt ")


def test_a_line_has_timestamp_level_component_event_and_fields(capsys):
    log("INFO", "svc", "evt", request="slack-1", note="two words", n=3, ok=True)
    out = capsys.readouterr().out.strip()
    assert LINE.match(out)
    assert out.endswith('request=slack-1 note="two words" n=3 ok=true')


def test_warnings_and_errors_go_to_stderr_and_a_missing_value_is_a_dash(capsys):
    log("WARN", "svc", "evt", request=None)
    log("ERROR", "svc", "evt")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "request=-" in captured.err.splitlines()[0]
    assert " ERROR [svc] evt" in captured.err.splitlines()[1]


def test_token_shapes_are_hidden():
    secrets = [
        "xoxb-123-456-abcdef", "ghp_abcdefghijklmnopqrstuvwxyz0123", "sk-abcdefghijklmnop", "sk-ant-api03-abc_def-ghi",
        "1000." + "a" * 32 + "." + "b" * 32, "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl",
    ]
    for secret in secrets:
        assert secret not in redact(f"failed with {secret} here"), secret


def test_labelled_secrets_are_hidden_including_in_url_queries():
    text = ("Authorization: Bearer abc123 token=t1 api_key=k1 secret=s1 password=p1 "
            "GET https://s3.example.com/c.html?X-Amz-Signature=sig1&X-Amz-Credential=cred1 failed")
    out = redact(text)
    for leaked in ("abc123", "t1", "k1", "s1", "p1", "sig1", "cred1"):
        assert leaked not in out, leaked
    assert "https://s3.example.com/c.html" in out and "failed" in out


def test_plain_text_is_kept_and_long_text_is_cut():
    assert redact("Jira returned 401: nope") == "Jira returned 401: nope"
    assert len(redact("x" * 1000)) <= 301


def test_concurrent_log_lines_never_join(monkeypatch):
    # print() is two writes, the text and then "\n"; two tool calls logging in
    # the same millisecond came out as one line (measured 2026-10-05).
    import io
    import sys
    import threading

    import time

    class Unbuffered(io.StringIO):
        # As in the containers (PYTHONUNBUFFERED=1): every write goes straight
        # out, and another thread can run between two of them.
        def write(self, text):
            time.sleep(0)
            return super().write(text)

    out = Unbuffered()
    monkeypatch.setattr(sys, "stdout", out)
    n_threads, per_thread = 16, 200
    barrier = threading.Barrier(n_threads)

    def hammer(n):
        barrier.wait()
        for i in range(per_thread):
            log("INFO", "mcp", "call", tool=f"t{n}", i=i)

    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        threads = [threading.Thread(target=hammer, args=(n,)) for n in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        sys.setswitchinterval(old)
    lines = out.getvalue().splitlines()
    assert len(lines) == n_threads * per_thread
    whole = re.compile(r"^\S+Z INFO \[mcp\] call tool=t\d+ i=\d+$")
    assert [line for line in lines if not whole.match(line)] == []
