#!/usr/bin/env python3
"""Читаемая версия итога для руководителя: ссылки [Cxxx] заменяются сносками [1], [2]...

Использование:
  python tools/render.py                                  # output/final.md -> output/final_readable.md
  python tools/render.py --root examples/task2            # то же для демо в своей папке
  python tools/render.py --in output/strategy.md --out output/strategy_readable.md

Что делает:
  - [C009] -> [1], [C009, C010] -> [1, 2]; номера по порядку первого появления в тексте;
  - пометки «допущение», «прогноз», «неизвестно» в тексте остаются как есть;
  - служебные пометки линтера <!-- ok --> убираются;
  - в блоках кода и `инлайн-коде` ничего не меняется;
  - в конец добавляется раздел «Основания»: текст утверждения, тип, источник
    (у факта source, у допущения и прогноза basis, у пробела where — где взять).

Коды выхода: 0 готово, 1 ссылка на несуществующий или отозванный claim (файл не пишется).
Только стандартная библиотека Python 3.8+.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REF_GROUP = re.compile(r"\[(C\d+(?:\s*,\s*C\d+)*)\]")
OK_MARK = re.compile(r"\s*<!--\s*ok\s*-->")
TYPE_RU = {"fact": "факт", "assumption": "допущение", "forecast": "прогноз", "unknown": "неизвестно"}


def load_claims(path):
    claims = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if raw:
            c = json.loads(raw)
            claims[c["id"]] = c
    return claims


def render(text, claims):
    """Возвращает (новый текст без раздела «Основания», список id по порядку сносок, ошибки)."""
    order, errors = {}, []

    def num(cid):
        if cid not in order:
            order[cid] = len(order) + 1
        return order[cid]

    def sub_refs(chunk):
        def repl(m):
            ids = [x.strip() for x in m.group(1).split(",")]
            for cid in ids:
                c = claims.get(cid)
                if c is None:
                    errors.append(f"ссылка на несуществующий {cid}")
                elif c.get("status") == "superseded":
                    errors.append(f"{cid} отозван (superseded)")
            return "[" + ", ".join(str(num(cid)) for cid in ids) + "]"
        return REF_GROUP.sub(repl, chunk)

    out, in_code = [], False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            out.append(line)
            continue
        if in_code:
            out.append(line)
            continue
        line = OK_MARK.sub("", line)
        # нечётные куски после split по ` это инлайн-код, их не трогаем
        parts = line.split("`")
        parts = [p if i % 2 else sub_refs(p) for i, p in enumerate(parts)]
        out.append("`".join(parts))
    return "\n".join(out).rstrip() + "\n", list(order), errors


def basis_line(c):
    def join(v):
        return "; ".join(map(str, v)) if isinstance(v, list) else str(v)
    t = c.get("type")
    parts = []
    if c.get("source"):
        parts.append("источник: " + join(c["source"]))
    if c.get("basis") and t != "fact":
        parts.append("основание: " + join(c["basis"]))
    if c.get("where"):
        parts.append("где взять: " + join(c["where"]))
    if not parts:
        parts.append("источника нет" if t != "fact" else "источник не указан")
    return "; ".join(parts)


def references(ids, claims):
    lines = ["", "## Основания", "",
             "Номер в квадратных скобках в тексте ведёт сюда. Тип: факт (есть источник), допущение, "
             "прогноз (расчёт на допущениях) или неизвестно (пробел в данных).", ""]
    for n, cid in enumerate(ids, 1):
        c = claims[cid]
        text = str(c.get("text", "")).strip().rstrip(".")
        ctype = TYPE_RU.get(c.get("type"), str(c.get("type")))
        lines.append(f"{n}. {text}. *{ctype.capitalize()}*; {basis_line(c)}. ({cid})")
    return "\n".join(lines) + "\n"


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="final.md -> final_readable.md со сносками")
    ap.add_argument("--root", help="корень проекта (например examples/task2); по умолчанию CLAUDE_PROJECT_DIR или репозиторий")
    ap.add_argument("--in", dest="src", default="output/final.md")
    ap.add_argument("--out", default=None, help="по умолчанию <имя>_readable.md рядом с исходным")
    ap.add_argument("--claims", default="state/claims.jsonl")
    a = ap.parse_args()
    root = Path(a.root).resolve() if a.root else Path(os.environ.get("CLAUDE_PROJECT_DIR") or ROOT)
    os.chdir(root)

    src = Path(a.src)
    out = Path(a.out) if a.out else src.with_name(src.stem + "_readable.md")
    claims = load_claims(Path(a.claims))
    body, ids, errors = render(src.read_text(encoding="utf-8"), claims)
    if errors:
        for e in dict.fromkeys(errors):
            print("ОШИБКА:", e, file=sys.stderr)
        return 1
    out.write_text(body + (references(ids, claims) if ids else ""), encoding="utf-8")
    print(f"ОК: {out} ({len(ids)} оснований)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
