import json

import pytest

from conftest import TOOLS, load_tool, run

cr = load_tool("cost_report")

PRICES = {"claude-test": {"base": {"input": 1, "cache_write_5m": 2, "cache_write_1h": 3,
                                   "cache_read": 0.5, "output": 10}}}


def assistant(rid, text, usage, model="claude-test", ts="2026-11-20T10:00:00Z"):
    return {"type": "assistant", "sessionId": "s1", "requestId": rid, "timestamp": ts,
            "message": {"id": "msg_" + rid, "model": model,
                        "content": [{"type": "text", "text": text}], "usage": usage}}


U1 = {"input_tokens": 100, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 2000,
      "output_tokens": 50, "cache_creation": {"ephemeral_5m_input_tokens": 400, "ephemeral_1h_input_tokens": 600}}
U2 = {"input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 5}


@pytest.fixture
def transcript(tmp_path):
    rows = [
        {"type": "user", "sessionId": "s1", "timestamp": "2026-11-20T09:59:00Z"},
        # один ответ модели разбит на несколько блоков, usage повторяется в каждом
        assistant("req_1", "часть 1", U1),
        assistant("req_1", "часть 2", U1),
        assistant("req_1", "tool_use", U1),
        assistant("req_2", "ответ", U2, ts="2026-11-20T10:05:00Z"),
        {"type": "system", "subtype": "turn_duration", "durationMs": 120000, "sessionId": "s1",
         "timestamp": "2026-11-20T10:05:01Z"},
    ]
    p = tmp_path / "s1.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\nне json\n", encoding="utf-8")
    return p


def test_usage_counted_once_per_request_id(transcript):
    s = cr.parse([transcript], PRICES)
    assert s["requests"] == 2
    assert s["models"] == {"claude-test": 2}
    assert s["input"] == 110
    assert s["cache_write"] == 1000
    assert s["cache_read"] == 2000
    assert s["output"] == 55
    usd = (110 * 1 + 400 * 2 + 600 * 3 + 2000 * 0.5 + 55 * 10) / 1e6
    assert s["usd"] == pytest.approx(usd)
    assert s["active_ms"] == 120000
    assert s["start"] == "2026-11-20T09:59:00Z"
    assert s["end"] == "2026-11-20T10:05:01Z"


def test_cli_file_mode(transcript):
    r = run(TOOLS / "cost_report.py", ["--file", str(transcript)], cwd=transcript.parent)
    assert r.returncode == 0, r.stderr
    row = json.loads(r.stdout)
    assert row["session_id"] == "s1"
    assert row["requests"] == 2
    assert row["output"] == 55


def test_hook_rerun_keeps_one_row_per_session(transcript, tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "COSTS", tmp_path / "state" / "costs.csv")
    s = cr.parse([transcript], PRICES)
    cr.upsert(cr.to_row(s, 5))
    cr.upsert(cr.to_row(s, ""))  # повторный запуск хука не задваивает и сохраняет шаг
    rows = cr.read_rows()
    assert len(rows) == 1
    assert rows[0]["requests"] == "2"
    assert rows[0]["step"] == "5"


def test_unknown_model_has_no_price(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps(assistant("r", "x", U2, model="other-model")) + "\n", encoding="utf-8")
    s = cr.parse([p], PRICES)
    assert s["requests"] == 1 and s["usd"] == 0
