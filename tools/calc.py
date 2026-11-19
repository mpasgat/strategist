#!/usr/bin/env python3
"""Воспроизводимые расчёты для стратегии. Только стандартная библиотека Python 3.8+.

Любой числовой параметр можно задать одним значением или парой «базовый/неблагоприятный»:
  --accept 0.6/0.4   ->  сценарий base = 0.6, сценарий unfavorable = 0.4
Расчёт выполняется для каждого сценария. Результат печатается и сохраняется
в output/calc/<name>.json и output/calc/<name>.md вместе со всеми входными допущениями.

Команды:
  funnel       воронка: создали -> приняли приглашение -> первое действие в срок
  breakeven    сколько доп. оплаченных месяцев нужно, чтобы закрыть разрыв в прибыли
  retention    доп. оплаченные месяцы в плановом году от снижения месячного оттока
  payback      окупаемость по кварталам или предельные вложения за срок
  sample-size  размер выборки для сравнения двух долей (с поправкой на кластеры) и срок набора
  delta        разница двух долей: процентные пункты и относительное изменение

Пример:
  python tools/calc.py funnel --name family_funnel --created 1000 --accept 0.6/0.45 --action 0.5/0.35 \\
      --note "доли приёма и действия: допущения, проверить на пилоте"
"""
import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from statistics import NormalDist

ROOT = Path(__file__).resolve().parents[1]
SCEN = ("base", "unfavorable")


def val(s):
    parts = str(s).split("/")
    if len(parts) > 2:
        raise argparse.ArgumentTypeError(f"ожидается число или пара base/unfavorable: {s}")
    try:
        nums = [float(p.replace(",", ".")) for p in parts]
    except ValueError:
        raise argparse.ArgumentTypeError(f"не число: {s}")
    return {"base": nums[0], "unfavorable": nums[-1]}


def seq(s):
    try:
        return [float(x.replace(",", ".")) for x in s.split(";") if x.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError(f"ожидается список через ';': {s}")


def share(x, name):
    if not 0 <= x <= 1:
        raise SystemExit(f"ошибка: {name} должна быть долей от 0 до 1, получено {x}")
    return x


def funnel(a, s):
    created = a.created[s]
    acc = share(a.accept[s], "--accept")
    act = share(a.action[s], "--action")
    accepted = created * acc
    activated = accepted * act
    return {
        "создали_групп": round(created),
        "приняли_приглашение": round(accepted),
        "выполнили_действие_в_срок": round(activated),
        "активация_от_созданных": round(activated / created, 4) if created else None,
        "пояснение": "знаменатель активации: все созданные группы, включая приглашения без ответа",
    }


def breakeven(a, s):
    gap = a.gap[s]
    margin = a.margin[s]
    costs = a.costs[s]
    other = a.other[s]
    need = (gap + costs - other) / margin
    return {
        "разрыв_руб": gap,
        "прямые_расходы_руб": costs,
        "другие_вклады_руб": other,
        "валовая_прибыль_за_доп_месяц_руб": margin,
        "нужно_доп_оплаченных_месяцев": math.ceil(max(need, 0)),
        "формула": "(разрыв + прямые расходы - другие вклады) / прибыль за доп. месяц",
    }


def retention(a, s):
    n = a.numbers[s]
    start = int(a.start_month[s])
    if not 1 <= start <= 12:
        raise SystemExit("ошибка: --start-month от 1 до 12")
    c0 = share(a.churn_base[s], "--churn-base")
    c1 = share(a.churn_new[s], "--churn-new")
    months = 12 - start + 1
    rows = []
    total = 0.0
    for k in range(1, months + 1):
        diff = n * ((1 - c1) ** k - (1 - c0) ** k)
        total += diff
        rows.append({"месяц": start + k - 1, "доп_оплаченных": round(diff, 1)})
    return {
        "номеров_с_услугой": n,
        "месяц_начала": start,
        "месяцев_в_плановом_году": months,
        "доп_оплаченных_месяцев": round(total),
        "вклад_руб": round(total * a.margin[s]),
        "по_месяцам": rows,
        "пояснение": "сумма по месяцам разницы числа платящих номеров при новом и обычном обслуживании; "
                     "месяцы после конца года не учитываются",
    }


def payback(a, s):
    profits = [p for p in a.quarterly_profit]
    k = a.factor[s]
    profits = [p * k for p in profits]
    cum, out, when = 0.0, [], None
    inv = a.investment[s] if a.investment else None
    for i, p in enumerate(profits, 1):
        cum += p
        out.append({"квартал": i, "прибыль_после_прямых_расходов": round(p), "накопленно": round(cum)})
        if inv is not None and when is None and cum >= inv:
            when = i
    res = {"по_кварталам": out, "предельные_вложения_за_горизонт_руб": round(cum),
           "множитель_сценария": k}
    if inv is not None:
        res["вложения_руб"] = inv
        res["окупается_в_квартале"] = when if when else "не окупается в горизонте расчёта"
    return res


def delta(p0, p1):
    pp = (p1 - p0) * 100
    rel = (p1 - p0) / p0 * 100 if p0 else None
    return {"процентных_пунктов": round(pp, 2),
            "относительно_исходного_процентов": round(rel, 1) if rel is not None else None}


def sample_size(a, s):
    p0 = share(a.p0[s], "--p0")
    p1 = share(a.p1[s], "--p1")
    if p0 == p1:
        raise SystemExit("ошибка: p0 и p1 совпадают, эффект нулевой")
    alpha, power = a.alpha, a.power
    z = NormalDist()
    za = z.inv_cdf(1 - alpha / 2)
    zb = z.inv_cdf(power)
    pbar = (p0 + p1) / 2
    n = (za * math.sqrt(2 * pbar * (1 - pbar)) + zb * math.sqrt(p0 * (1 - p0) + p1 * (1 - p1))) ** 2 / (p1 - p0) ** 2
    m = a.cluster_size[s]
    icc = a.icc[s]
    deff = 1 + (m - 1) * icc
    n_units = math.ceil(n * deff)
    clusters = math.ceil(n_units / m)
    res = {
        "единица_наблюдения": a.unit,
        "p0": p0, "p1": p1, "разница": delta(p0, p1),
        "alpha": alpha, "мощность": power,
        "без_поправки_на_группу": math.ceil(n),
        "размер_кластера": m, "icc": icc, "design_effect": round(deff, 3),
        "нужно_единиц_в_каждой_ветке": n_units,
        "нужно_кластеров_в_каждой_ветке": clusters,
        "нужно_кластеров_всего": clusters * 2,
        "формула": "две доли, нормальное приближение; поправка 1 + (m-1)*ICC при назначении варианта всей группе",
    }
    if a.weekly_inflow:
        w = a.weekly_inflow[s]
        weeks = math.ceil(clusters * 2 / w) if w > 0 else None
        res["впервые_подходящих_кластеров_в_неделю"] = w
        res["недель_набора"] = weeks
        res["недель_наблюдения_после_набора"] = a.observe_weeks
        res["недель_до_вывода"] = weeks + a.observe_weeks if weeks else None
    return res


def cmd_delta(a, s):
    return delta(share(a.p0[s], "--p0"), share(a.p1[s], "--p1"))


COMMANDS = {
    "funnel": funnel, "breakeven": breakeven, "retention": retention,
    "payback": payback, "sample-size": sample_size, "delta": cmd_delta,
}


def build_parser():
    ap = argparse.ArgumentParser(description="Расчёты стратегии (base / unfavorable)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--name", help="имя файла результата (по умолчанию имя команды)")
        p.add_argument("--note", default="", help="откуда взяты входные значения (допущения, источники)")
        p.add_argument("--claims", default="", help="id claims, на которые опираются входы, через запятую")
        p.add_argument("--no-save", action="store_true", help="только напечатать")

    p = sub.add_parser("funnel"); common(p)
    p.add_argument("--created", type=val, required=True, help="создано групп")
    p.add_argument("--accept", type=val, required=True, help="доля групп, принявших приглашение")
    p.add_argument("--action", type=val, required=True, help="доля принявших, выполнивших действие в срок")

    p = sub.add_parser("breakeven"); common(p)
    p.add_argument("--gap", type=val, required=True, help="разрыв до цели, руб")
    p.add_argument("--margin", type=val, required=True, help="валовая прибыль за доп. оплаченный месяц, руб")
    p.add_argument("--costs", type=val, default=val("0"), help="новые прямые расходы, руб")
    p.add_argument("--other", type=val, default=val("0"), help="другие доходы или экономия, руб")

    p = sub.add_parser("retention"); common(p)
    p.add_argument("--numbers", type=val, required=True, help="номеров, которым стала доступна услуга")
    p.add_argument("--start-month", type=val, required=True, help="месяц запуска 1..12")
    p.add_argument("--churn-base", type=val, required=True, help="месячный отток при обычном обслуживании")
    p.add_argument("--churn-new", type=val, required=True, help="месячный отток при новом обслуживании")
    p.add_argument("--margin", type=val, default=val("500"), help="прибыль за доп. месяц, руб")

    p = sub.add_parser("payback"); common(p)
    p.add_argument("--quarterly-profit", type=seq, required=True, help="прибыль после прямых расходов по кварталам: 0;1e6;2e6")
    p.add_argument("--investment", type=val, help="вложения, руб; без него считаются предельные вложения")
    p.add_argument("--factor", type=val, default=val("1"), help="множитель прибыли по сценарию, например 1/0.6")

    for name in ("sample-size", "delta"):
        p = sub.add_parser(name); common(p)
        p.add_argument("--p0", type=val, required=True, help="доля в группе сравнения")
        p.add_argument("--p1", type=val, required=True, help="ожидаемая доля при изменении")
        if name == "sample-size":
            p.add_argument("--alpha", type=float, default=0.05)
            p.add_argument("--power", type=float, default=0.8)
            p.add_argument("--unit", default="группа", help="единица наблюдения")
            p.add_argument("--cluster-size", type=val, default=val("1"), help="единиц в кластере (номеров в семье)")
            p.add_argument("--icc", type=val, default=val("0"), help="внутрикластерная корреляция")
            p.add_argument("--weekly-inflow", type=val, help="впервые подходящих кластеров в неделю")
            p.add_argument("--observe-weeks", type=int, default=0, help="наблюдение за последними участниками, недель")
    return ap


def to_md(name, a, inputs, result):
    lines = [f"# Расчёт: {name}", "", f"Команда: `{a.cmd}`, {result['_meta']['time']}", ""]
    if a.note:
        lines += [f"Основание входов: {a.note}", ""]
    if a.claims:
        lines += [f"Опирается на: {a.claims}", ""]
    lines += ["## Входы (base / unfavorable)", ""]
    for k, v in inputs.items():
        lines.append(f"- {k}: {v}")
    for s in SCEN:
        lines += ["", f"## Сценарий {s}", "", "```json", json.dumps(result[s], ensure_ascii=False, indent=1), "```"]
    return "\n".join(lines) + "\n"


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    a = build_parser().parse_args(argv)
    inputs = {}
    for k, v in vars(a).items():
        if k in ("cmd", "name", "note", "claims", "no_save"):
            continue
        if isinstance(v, dict):
            inputs[k] = v["base"] if v["base"] == v["unfavorable"] else f"{v['base']} / {v['unfavorable']}"
        elif v is not None:
            inputs[k] = v
    fn = COMMANDS[a.cmd]
    result = {s: fn(a, s) for s in SCEN}
    result["_meta"] = {"command": a.cmd, "inputs": inputs, "note": a.note, "claims": a.claims,
                       "time": datetime.now().isoformat(timespec="seconds"),
                       "argv": sys.argv[1:] if argv is None else argv}
    name = a.name or a.cmd
    if not a.no_save:
        d = ROOT / "output" / "calc"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        (d / f"{name}.md").write_text(to_md(name, a, inputs, result), encoding="utf-8")
        result["_meta"]["saved"] = f"output/calc/{name}.json"
    print(json.dumps({k: result[k] for k in (*SCEN, "_meta")}, ensure_ascii=False, indent=1))
    return result


if __name__ == "__main__":
    main()
