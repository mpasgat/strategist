#!/usr/bin/env python3
"""Проверка дисциплины фактов: реестр claims и черновики в output/.

Использование:
  python tools/lint_claims.py output/strategy.md       # проверить один черновик
  python tools/lint_claims.py                          # проверить только реестр claims
  python tools/lint_claims.py --hook                   # режим хука: output/strategy.md и final.md, с шага 5

Что проверяется:
  реестр:   уникальные id, допустимый тип, у fact есть source, файл источника существует.
  черновик: все [Cxxx] есть в реестре; ссылка не ведёт на отозванную запись (superseded);
            строка со ссылкой на assumption/forecast/unknown содержит слово-метку
            (допущение / прогноз / неизвестно);
            строка с числом без ссылки на claim считается ошибкой (годы, Q1-Q4, «шаг N»,
            «вариант N», «СТОП N», «часть N», «раздел N» числами не считаются);
            якорь источника (#p4, #r2, #s3) существует в файле из project/sources.
  Исключение для строки: добавьте в конец <!-- ok -->.

Коды выхода: 0 всё хорошо, 1 есть ошибки, 2 есть ошибки в режиме --hook (блокирует остановку агента).
Только стандартная библиотека Python 3.8+.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

TYPES = {"fact", "assumption", "forecast", "unknown"}
LABELS = {
    "assumption": ("допущен",),
    "forecast": ("прогноз",),
    "unknown": ("неизвестн", "пробел", "не знаем"),
}
REF_GROUP = re.compile(r"\[(C\d+(?:\s*,\s*C\d+)*)\]")
MAX_PRINT = 30
ROOT = Path(__file__).resolve().parents[1]
HOOK_FILES = ("output/strategy.md", "output/final.md")
HOOK_FROM_STEP = 5
NOT_NUMBERS = re.compile(
    r"\b(?:19|20)\d\d\b|\bQ[1-4]\b|\b[1-4]\s*кв(?:артал\w*)?\b|C\d+|"
    r"(?:шаг\w*|вариант\w*|стоп|част\w*|раздел\w*|пункт\w*|п\.)\s*\d+|"
    r"\b\d+\s*(?:вариант\w*|шаг\w*|остановк\w*)",
    re.IGNORECASE,
)


def load_claims(path, errors, warnings):
    claims = {}
    if not path.exists():
        warnings.append(f"{path}: файла нет (реестр пуст)")
        return claims
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            c = json.loads(raw)
        except json.JSONDecodeError as e:
            errors.append(f"{path.name}:{n}: невалидный JSON ({e.msg})")
            continue
        cid = c.get("id")
        if not cid or not re.fullmatch(r"C\d+", str(cid)):
            errors.append(f"{path.name}:{n}: id должен быть вида C001")
            continue
        if cid in claims:
            errors.append(f"{path.name}:{n}: повторный id {cid}")
            continue
        ctype = c.get("type")
        if ctype not in TYPES:
            errors.append(f"{cid}: тип '{ctype}' недопустим, нужен один из {sorted(TYPES)}")
        if not str(c.get("text", "")).strip():
            errors.append(f"{cid}: пустой text")
        if ctype == "fact":
            src = c.get("source")
            srcs = src if isinstance(src, list) else [src]
            if not src or not all(srcs):
                errors.append(f"{cid}: у fact нет source")
            else:
                for s in srcs:
                    s = str(s)
                    if s.startswith(("http://", "https://")):
                        continue
                    fpath, _, anchor = s.partition("#")
                    if not Path(fpath).exists():
                        errors.append(f"{cid}: файл источника не найден: {fpath}")
                    elif anchor and fpath.endswith(".md"):
                        if f'id="{anchor}"' not in Path(fpath).read_text(encoding="utf-8"):
                            errors.append(f"{cid}: в {fpath} нет метки #{anchor}")
        if ctype in ("assumption", "forecast") and not c.get("basis"):
            warnings.append(f"{cid}: у {ctype} нет поля basis (на чём держится)")
        claims[cid] = c
    return claims


def lint_draft(path, claims, errors, warnings):
    used = set()
    in_code = False
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if s.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not s or s.startswith("#") or re.fullmatch(r"[\s|:\-]+", s):
            continue
        if "<!-- ok -->" in s:
            continue
        ids = []
        for grp in REF_GROUP.findall(s):
            ids += [x.strip() for x in grp.split(",")]
        where = f"{path.name}:{n}"
        for cid in ids:
            used.add(cid)
            c = claims.get(cid)
            if c is None:
                errors.append(f"{where}: ссылка на несуществующий {cid}")
                continue
            if c.get("status") == "superseded":
                errors.append(f"{where}: {cid} отозван (superseded), обновите вывод")
            labels = LABELS.get(c.get("type"))
            if labels and not any(w in s.lower() for w in labels):
                errors.append(f"{where}: {cid} это {c['type']}, добавьте в строку слово «{labels[0]}…»")
        body = NOT_NUMBERS.sub("", REF_GROUP.sub("", s))
        body = re.sub(r"^\s*(?:[-*]\s+|\d+[.)]\s+)", "", body)
        if not ids and re.search(r"\d", body):
            errors.append(f"{where}: есть число, но нет ссылки [Cxxx]: {s[:60]}")
    return used


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("draft", nargs="*", help="черновики .md")
    ap.add_argument("--claims", default="state/claims.jsonl")
    ap.add_argument("--hook", action="store_true", help="режим хука Claude Code")
    a = ap.parse_args()
    os.chdir(os.environ.get("CLAUDE_PROJECT_DIR") or ROOT)

    if a.hook:
        try:
            data = json.load(sys.stdin)
            if data.get("stop_hook_active"):
                return 0  # не зацикливаем остановку
        except Exception:
            pass
        try:
            step = int(json.loads(Path("state/task.json").read_text(encoding="utf-8")).get("step", 0))
        except Exception:
            step = 0
        if step < HOOK_FROM_STEP:
            return 0  # на шагах 1-4 и на остановках для человека не тратим лишний круг
        drafts = [Path(f) for f in HOOK_FILES if Path(f).exists()]
    else:
        drafts = [Path(d) for d in a.draft]

    errors, warnings = [], []
    claims = load_claims(Path(a.claims), errors, warnings)
    used = set()
    for d in drafts:
        if not d.exists():
            errors.append(f"{d}: файла нет")
            continue
        used |= lint_draft(d, claims, errors, warnings)
    if drafts:
        unused = [k for k, v in claims.items() if k not in used and v.get("status") != "superseded"]
        if unused:
            warnings.append(f"записи без использования в черновиках: {', '.join(unused[:10])}")

    out = sys.stderr if (a.hook and errors) else sys.stdout
    for e in errors[:MAX_PRINT]:
        print("ОШИБКА:", e, file=out)
    if len(errors) > MAX_PRINT:
        print(f"... и ещё {len(errors) - MAX_PRINT} ошибок", file=out)
    for w in warnings[:10]:
        print("внимание:", w, file=out)
    print(f"{'ОК' if not errors else 'ОШИБОК: ' + str(len(errors))}; claims: {len(claims)}, черновиков: {len(drafts)}", file=out)
    if errors:
        return 2 if a.hook else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
