import re

from textql_agent.log import log


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
