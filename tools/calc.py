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
  mix          падение общей доли: вклад смены структуры групп и вклад изменения долей внутри групп
  trial-value  ценность пробного аккаунта в горизонте: оплаченные месяцы, валовая прибыль, расходы
  activation-payback  окупаемость роста активации от даты вложения: месячные когорты, кварталы, нужный прирост

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


def parse_groups(text):
    out = {}
    for part in text.split(";"):
        if not part.strip():
            continue
        name, _, nums = part.partition("=")
        n, _, k = nums.partition(":")
        try:
            out[name.strip()] = (float(n), float(k))
        except ValueError:
            raise SystemExit(f"ошибка: группа '{part}' должна быть вида имя=всего:успехов")
    return out


def mix(a, s):
    g0, g1 = parse_groups(a.base), parse_groups(a.current)
    if set(g0) != set(g1):
        raise SystemExit("ошибка: в --base и --current должны быть одни и те же группы")
    n0 = sum(v[0] for v in g0.values())
    n1 = sum(v[0] for v in g1.values())
    rate0 = sum(v[1] for v in g0.values()) / n0
    rate1 = sum(v[1] for v in g1.values()) / n1
    groups = {}
    std = 0.0
    for k in g0:
        r0, r1 = g0[k][1] / g0[k][0], g1[k][1] / g1[k][0]
        std += g0[k][0] / n0 * r1
        groups[k] = {"доля_в_базе": round(g0[k][0] / n0, 4), "доля_сейчас": round(g1[k][0] / n1, 4),
                     "успех_в_базе": round(r0, 4), "успех_сейчас": round(r1, 4)}
    return {
        "группы": groups,
        "общая_доля_база": round(rate0, 4),
        "общая_доля_сейчас": round(rate1, 4),
        "сейчас_при_структуре_базы": round(std, 4),
        "изменение_всего_пп": round((rate1 - rate0) * 100, 2),
        "вклад_структуры_пп": round((rate1 - std) * 100, 2),
        "вклад_долей_внутри_групп_пп": round((std - rate0) * 100, 2),
        "формула": "сейчас_при_структуре_базы = сумма(доля группы в базе * успех группы сейчас); "
                   "вклад структуры = общая сейчас - она; вклад долей = она - общая в базе",
    }


def trial_value(a, s):
    conv = share(a.convert[s], "--convert")
    keep = share(a.retain[s], "--retain")
    act = share(a.active[s], "--active")
    t, h, n = int(a.trial_months[s]), int(a.horizon[s]), a.accounts[s]
    paid_span = max(h - t, 0)
    surv = sum(keep ** k for k in range(paid_span))
    paid = conv * surv
    gp = paid * a.margin[s]
    free = act * t * a.free_cost[s]
    acq = a.acq_cost[s]
    net = gp - free - acq
    res = {
        "аккаунтов": n,
        "пробный_месяцев": t, "горизонт_месяцев": h, "платных_месяцев_в_горизонте": paid_span,
        "оплаченных_месяцев_на_аккаунт": round(paid, 3),
        "валовая_прибыль_на_аккаунт_руб": round(gp, 1),
        "расход_на_бесплатный_период_на_аккаунт_руб": round(free, 1),
        "привлечение_на_аккаунт_руб": acq,
        "итог_на_аккаунт_руб": round(net, 1),
        "итог_всего_руб": round(net * n),
        "формула": "итог = конверсия * сумма(удержание^k, k=0..платных_месяцев-1) * прибыль "
                   "- активные * пробных_месяцев * расход_на_бесплатный_месяц - привлечение",
    }
    if a.target is not None and surv > 0:
        need = (a.target[s] + free + acq) / (a.margin[s] * surv)
        res["цель_итога_на_аккаунт_руб"] = a.target[s]
        res["нужна_конверсия_для_цели"] = round(need, 4)
    return res


def _uplift_per_account(h, lift, pay, keep, fc, t, margin):
    """Накопленный прирост итога на один аккаунт к концу h-го месяца от его начала."""
    if h <= 0:
        return 0.0
    free = lift * min(h, t) * fc
    paid = lift * pay * sum(keep ** k for k in range(max(h - t, 0)))
    return paid * margin - free


def _cum_from_investment(month, n, lag, lift, pay, keep, fc, t, margin):
    """Накопленный прирост к концу month-го месяца от вложения; когорты по n аккаунтов
    начинаются каждый месяц, первая в месяце lag + 1."""
    return sum(n * _uplift_per_account(month - m + 1, lift, pay, keep, fc, t, margin)
               for m in range(lag + 1, month + 1))


def activation_payback(a, s):
    n = a.accounts_per_month[s]
    lift = share(a.upload_lift[s], "--upload-lift")
    pay = share(a.pay_share[s], "--pay-share")
    keep = share(a.retain[s], "--retain")
    fc, margin = a.free_cost[s], a.margin[s]
    t, lag, h = int(a.trial_months[s]), int(a.lag[s]), int(a.horizon[s])
    if h % 3:
        raise SystemExit("ошибка: --horizon должен делиться на 3 (считаем по кварталам)")
    cum = lambda m, d=lift: _cum_from_investment(m, n, lag, d, pay, keep, fc, t, margin)
    quarters, prev, when = [], 0.0, None
    inv = a.investment[s] if a.investment else None
    for q in range(1, h // 3 + 1):
        c = cum(3 * q)
        quarters.append({"квартал": q, "прирост_руб": round(c - prev), "накопленно": round(c)})
        if inv is not None and when is None and c >= inv:
            when = q
        prev = c
    res = {
        "аккаунтов_в_месяц": n, "прирост_доли_загрузивших": lift, "платят_из_новых_загрузивших": pay,
        "месяцев_до_запуска": lag, "горизонт_от_вложения_месяцев": h,
        "по_кварталам": quarters, "накопленно_за_горизонт_руб": round(prev),
        "формула": "прирост аккаунта к месяцу h = прирост_загрузки * платят * сумма(удержание^k, k=0..h-пробный-1) * прибыль "
                   "- прирост_загрузки * min(h, пробный) * расход_на_бесплатный_месяц; "
                   "сумма по месячным когортам, первая когорта в месяце (до_запуска + 1) от вложения",
    }
    if inv is not None:
        res["вложения_руб"] = inv
        res["окупается_в_квартале"] = when if when else "не окупается в горизонте расчёта"
    if a.budgets:
        need = {}
        for b in a.budgets:
            if cum(h, 1.0) < b:
                need[str(round(b))] = "не окупается даже при приросте 100 п.п."
                continue
            lo, hi = 0.0, 1.0
            for _ in range(50):
                mid = (lo + hi) / 2
                lo, hi = (lo, mid) if cum(h, mid) >= b else (mid, hi)
            need[str(round(b))] = round(hi * 100, 1)
        res["нужен_прирост_загрузки_пп_для_окупаемости"] = need
    return res


def cmd_delta(a, s):
    return delta(share(a.p0[s], "--p0"), share(a.p1[s], "--p1"))


COMMANDS = {
    "funnel": funnel, "breakeven": breakeven, "retention": retention,
    "payback": payback, "sample-size": sample_size, "delta": cmd_delta,
    "mix": mix, "trial-value": trial_value, "activation-payback": activation_payback,
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

    p = sub.add_parser("mix"); common(p)
    p.add_argument("--base", required=True, help="группы базового периода: имя=всего:успехов;имя=всего:успехов")
    p.add_argument("--current", required=True, help="те же группы в текущем периоде")

    p = sub.add_parser("trial-value"); common(p)
    p.add_argument("--convert", type=val, required=True, help="доля аккаунтов, платящих в первый платный месяц")
    p.add_argument("--retain", type=val, required=True, help="месячное удержание платящих после первого платного месяца")
    p.add_argument("--trial-months", type=val, required=True, help="бесплатных месяцев")
    p.add_argument("--horizon", type=val, default=val("12"), help="горизонт от начала пробного, месяцев")
    p.add_argument("--margin", type=val, required=True, help="валовая прибыль за оплаченный месяц, руб")
    p.add_argument("--active", type=val, default=val("0"), help="доля аккаунтов, пользующихся в пробный период")
    p.add_argument("--free-cost", type=val, default=val("0"), help="расход на активный аккаунт за бесплатный месяц, руб")
    p.add_argument("--acq-cost", type=val, default=val("0"), help="расход на привлечение одного аккаунта, руб")
    p.add_argument("--accounts", type=val, default=val("1"), help="аккаунтов в когорте")
    p.add_argument("--target", type=val, help="итог на аккаунт, руб, для расчёта нужной конверсии")

    p = sub.add_parser("activation-payback"); common(p)
    p.add_argument("--accounts-per-month", type=val, required=True, help="новых аккаунтов в месяц")
    p.add_argument("--upload-lift", type=val, required=True, help="прирост доли загрузивших, доля (0.15 = +15 п.п.)")
    p.add_argument("--pay-share", type=val, required=True, help="доля платящих в первый платный месяц среди новых загрузивших")
    p.add_argument("--retain", type=val, required=True, help="месячное удержание платящих")
    p.add_argument("--margin", type=val, required=True, help="валовая прибыль за оплаченный месяц, руб")
    p.add_argument("--free-cost", type=val, default=val("0"), help="расход на активный аккаунт за бесплатный месяц, руб")
    p.add_argument("--trial-months", type=val, default=val("3"), help="бесплатных месяцев")
    p.add_argument("--lag", type=val, default=val("0"), help="месяцев от вложения до запуска")
    p.add_argument("--horizon", type=val, default=val("12"), help="горизонт от вложения, месяцев (кратно 3)")
    p.add_argument("--investment", type=val, help="вложения, руб")
    p.add_argument("--budgets", type=seq, help="бюджеты для расчёта нужного прироста: 300000;600000")

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
