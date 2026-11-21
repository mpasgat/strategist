#!/usr/bin/env python3
"""Учёт расходов и времени по транскриптам Claude Code. Только стандартная библиотека.

Claude Code пишет каждую сессию в ~/.claude/projects/<папка-проекта>/<session_id>.jsonl.
Скрипт берёт оттуда реальные счётчики токенов (поле usage ответов модели) и время ходов
(записи turn_duration), а не оценки модели.

  python tools/cost_report.py --hook      # из Stop-хука: обновить строку текущей сессии в state/costs.csv
  python tools/cost_report.py --scan      # пересобрать state/costs.csv по всем сессиям этого проекта
  python tools/cost_report.py             # сводка по state/costs.csv
  python tools/cost_report.py --file X.jsonl   # разобрать один транскрипт и напечатать итог

Стоимость usd_equiv это эквивалент по ценам API из tools/prices.json. При подписке (Pro, Max)
реальные деньги это цена тарифа, а usd_equiv показывает, сколько стоила бы та же работа через API.
Повторный запуск не задваивает расходы: строка сессии перезаписывается, запросы считаются по requestId.
"""
import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COSTS = ROOT / "state" / "costs.csv"
PRICES = Path(__file__).resolve().parent / "prices.json"
FIELDS = ["session_id", "step", "start", "end", "active_min", "requests", "models",
          "input", "cache_write", "cache_read", "output", "usd_equiv", "updated"]


def projects_dir():
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(ROOT))
    return base / slug


def load_prices():
    try:
        return json.loads(PRICES.read_text(encoding="utf-8"))["models"]
    except Exception:
        return {}


def price_for(model, prompt_len, prices):
    for key, p in prices.items():
        if model and model.startswith(key):
            if "over_tokens" in p and prompt_len > p["over_tokens"]:
                return p["over"]
            return p["base"]
    return None


def transcript_files(main):
    """Основной транскрипт и транскрипты субагентов, если Claude Code хранит их рядом."""
    files = [main]
    side = main.with_suffix("")
    if side.is_dir():
        files += sorted(side.rglob("*.jsonl"))
    return files


def parse(paths, prices):
    seen, warn = set(), set()
    s = {"start": None, "end": None, "active_ms": 0, "requests": 0, "models": {},
         "input": 0, "cache_write": 0, "cache_read": 0, "output": 0, "usd": 0.0, "session_id": None}
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw in lines:
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            s["session_id"] = s["session_id"] or d.get("sessionId")
            ts = d.get("timestamp")
            if ts:
                s["start"] = min(s["start"] or ts, ts)
                s["end"] = max(s["end"] or ts, ts)
            if d.get("type") == "system" and d.get("subtype") == "turn_duration":
                s["active_ms"] += d.get("durationMs") or 0
            if d.get("type") != "assistant":
                continue
            m = d.get("message") or {}
            rid = d.get("requestId") or m.get("id")
            u = m.get("usage")
            if not u or rid in seen:
                continue
            seen.add(rid)
            model = m.get("model", "?")
            cc = u.get("cache_creation") or {}
            w1h = cc.get("ephemeral_1h_input_tokens", 0)
            w5m = cc.get("ephemeral_5m_input_tokens", u.get("cache_creation_input_tokens", 0) - w1h)
            inp, read, out = u.get("input_tokens", 0), u.get("cache_read_input_tokens", 0), u.get("output_tokens", 0)
            s["requests"] += 1
            s["models"][model] = s["models"].get(model, 0) + 1
            s["input"] += inp
            s["cache_write"] += w1h + w5m
            s["cache_read"] += read
            s["output"] += out
            p = price_for(model, inp + w1h + w5m + read, prices)
            if p is None:
                warn.add(model)
                continue
            s["usd"] += (inp * p["input"] + w5m * p["cache_write_5m"] + w1h * p["cache_write_1h"]
                         + read * p["cache_read"] + out * p["output"]) / 1e6
    for model in sorted(warn):
        print(f"внимание: нет цены для {model} в tools/prices.json, стоимость не учтена", file=sys.stderr)
    return s


def current_step():
    try:
        return json.loads((ROOT / "state" / "task.json").read_text(encoding="utf-8")).get("step", "")
    except Exception:
        return ""


def to_row(s, step):
    return {
        "session_id": s["session_id"] or "",
        "step": step,
        "start": s["start"] or "",
        "end": s["end"] or "",
        "active_min": round(s["active_ms"] / 60000, 1),
        "requests": s["requests"],
        "models": " ".join(f"{k}:{v}" for k, v in sorted(s["models"].items())),
        "input": s["input"],
        "cache_write": s["cache_write"],
        "cache_read": s["cache_read"],
        "output": s["output"],
        "usd_equiv": round(s["usd"], 4),
        "updated": datetime.now().isoformat(timespec="seconds"),
    }


def read_rows():
    if not COSTS.exists():
        return []
    with COSTS.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_rows(rows):
    COSTS.parent.mkdir(parents=True, exist_ok=True)
    tmp = COSTS.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(COSTS)


def upsert(row):
    rows = [r for r in read_rows() if r["session_id"] != row["session_id"]]
    old = next((r for r in read_rows() if r["session_id"] == row["session_id"]), None)
    if old and old.get("step") and not row["step"]:
        row["step"] = old["step"]
    rows.append(row)
    rows.sort(key=lambda r: r["start"])
    write_rows(rows)


def summary():
    rows = read_rows()
    if not rows:
        print("state/costs.csv пуст. Запустите --scan или поработайте с включённым хуком.")
        return
    tot = {k: sum(float(r[k] or 0) for r in rows) for k in ("active_min", "requests", "input", "cache_write", "cache_read", "output", "usd_equiv")}
    models = {}
    for r in rows:
        for part in r["models"].split():
            name, _, n = part.rpartition(":")
            models[name] = models.get(name, 0) + int(n)
    print(f"сессий: {len(rows)}, активное время агента: {tot['active_min']:.0f} мин, запросов к модели: {tot['requests']:.0f}")
    print(f"токены: вход {tot['input']:.0f}, запись в кэш {tot['cache_write']:.0f}, чтение из кэша {tot['cache_read']:.0f}, выход {tot['output']:.0f}")
    print(f"эквивалент по ценам API: ${tot['usd_equiv']:.2f}")
    print("запросов по моделям: " + ", ".join(f"{k} {v}" for k, v in sorted(models.items())))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--hook", action="store_true")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--file")
    a = ap.parse_args()
    prices = load_prices()

    if a.hook:
        try:
            data = json.load(sys.stdin)
            path = Path(data["transcript_path"])
        except Exception:
            return 0
        if path.exists():
            upsert(to_row(parse(transcript_files(path), prices), current_step()))
        return 0  # учёт никогда не блокирует работу агента

    if a.file:
        s = parse(transcript_files(Path(a.file)), prices)
        print(json.dumps(to_row(s, ""), ensure_ascii=False, indent=1))
        return 0

    if a.scan:
        d = projects_dir()
        files = sorted(d.glob("*.jsonl")) if d.is_dir() else []
        if not files:
            print(f"транскриптов нет в {d}")
            return 1
        old = {r["session_id"]: r.get("step", "") for r in read_rows()}
        rows = []
        for f in files:
            s = parse(transcript_files(f), prices)
            if s["requests"]:
                rows.append(to_row(s, old.get(s["session_id"], "")))
        rows.sort(key=lambda r: r["start"])
        write_rows(rows)
        print(f"пересобрано: {len(rows)} сессий из {d}")
    summary()
    return 0


if __name__ == "__main__":
    sys.exit(main())
