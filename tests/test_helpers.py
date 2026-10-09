#!/usr/bin/env python3
"""Unit tests for the pure helpers in bundle/notebooks/01_graph_delta_ingest.py (no Spark, no network).

    python3 tests/test_helpers.py
"""
import email.utils
import pathlib
import re
import time

NB = pathlib.Path(__file__).resolve().parent.parent / "bundle/notebooks/01_graph_delta_ingest.py"
src = NB.read_text()
block = src.split("# --- helpers (pure; unit-tested in tests/test_helpers.py)")[1].split("# --- end helpers")[0]
block = block.split("\n", 1)[1]                # drop the remainder of the marker line
ns = {"re": re, "time": time, "email": email}
exec(block, ns)  # noqa: S102 - our own file


def test_sql_str():
    f = ns["sql_str"]
    assert f("plain") == "plain"
    assert f("it's") == "it''s"
    # backslash must be escaped too: Spark treats \' as an escaped quote
    assert f("x\\' OR 1=1 --") == "x\\\\'' OR 1=1 --"


def test_valid_graph_id():
    f = ns["valid_graph_id"]
    assert f("contoso.sharepoint.com,1111-2222,3333-4444", "site") == "contoso.sharepoint.com,1111-2222,3333-4444"
    assert f("b!AbC_dEf-123", "drive") == "b!AbC_dEf-123"
    for bad in ("", "a b", "x'; DROP", "../etc", None):
        try:
            f(bad, "id")
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_retry_after():
    f = ns["retry_after_seconds"]
    assert f("7", 0) == 7
    assert f(None, 3) == 8                      # no header -> exponential backoff
    assert f("garbage", 2) == 4
    assert f("100000", 0) == 300                # capped
    future = email.utils.formatdate(time.time() + 30, usegmt=True)
    assert 20 <= f(future, 0) <= 31             # HTTP-date form
    past = email.utils.formatdate(time.time() - 100, usegmt=True)
    assert f(past, 0) == 0


def test_safe_name():
    f = ns["safe_name"]
    assert f("report.pdf") == "report.pdf"
    long_ascii = "a" * 300 + ".docx"
    out = f(long_ascii)
    assert out.endswith(".docx") and len(out.encode()) <= 120
    multibyte = "é" * 200 + ".pdf"               # 2 bytes per char
    out = f(multibyte)
    assert out.endswith(".pdf") and len(out.encode()) <= 120
    out.encode("utf-8")                         # must stay valid UTF-8
    assert len(f("x" * 300).encode()) <= 120    # no extension


def test_dedupe_last():
    f = ns["dedupe_last"]
    items = [{"id": "1", "v": "old"}, {"id": "2", "v": "b"}, {"id": "1", "v": "new"}]
    out = f(items)
    assert len(out) == 2 and {i["id"]: i["v"] for i in out} == {"1": "new", "2": "b"}


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok  ", t.__name__)
    print(f"{len(tests)} passed")
