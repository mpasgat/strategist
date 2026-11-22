import json

import pytest

from conftest import load_tool

calc = load_tool("calc")


def run_calc(*argv):
    return calc.main([*argv, "--no-save"])


def test_breakeven():
    r = run_calc("breakeven", "--gap", "6000000", "--margin", "500")
    assert r["base"]["нужно_доп_оплаченных_месяцев"] == 12000
    assert r["unfavorable"]["нужно_доп_оплаченных_месяцев"] == 12000


def test_breakeven_rounds_up_and_uses_costs():
    r = run_calc("breakeven", "--gap", "1000", "--margin", "300", "--costs", "0/500")
    assert r["base"]["нужно_доп_оплаченных_месяцев"] == 4         # 1000/300 = 3.33 -> 4
    assert r["unfavorable"]["нужно_доп_оплаченных_месяцев"] == 5  # 1500/300 = 5


def test_funnel():
    r = run_calc("funnel", "--created", "100", "--accept", "0.6", "--action", "0.5")
    b = r["base"]
    assert b["приняли_приглашение"] == 60
    assert b["выполнили_действие_в_срок"] == 30
    assert b["активация_от_созданных"] == pytest.approx(0.3)


def test_funnel_pair_gives_two_scenarios():
    r = run_calc("funnel", "--created", "1000", "--accept", "0.6/0.4", "--action", "0.5")
    assert r["base"]["выполнили_действие_в_срок"] == 300
    assert r["unfavorable"]["выполнили_действие_в_срок"] == 200


def test_delta_points_and_percent():
    r = run_calc("delta", "--p0", "0.12", "--p1", "0.09")
    assert r["base"]["процентных_пунктов"] == pytest.approx(-3)
    assert r["base"]["относительно_исходного_процентов"] == pytest.approx(-25)


def test_retention_launch_in_month_12_at_most_one_month_per_number():
    n = 1000
    r = run_calc("retention", "--numbers", str(n), "--start-month", "12",
                 "--churn-base", "0.05", "--churn-new", "0.0")
    b = r["base"]
    assert b["месяцев_в_плановом_году"] == 1
    assert len(b["по_месяцам"]) == 1
    assert 0 < b["доп_оплаченных_месяцев"] <= n


def test_retention_full_year_bounded_by_months():
    n = 1000
    r = run_calc("retention", "--numbers", str(n), "--start-month", "1",
                 "--churn-base", "0.5", "--churn-new", "0.0")
    assert r["base"]["доп_оплаченных_месяцев"] <= n * 12


@pytest.mark.parametrize("cmd,args", [
    ("funnel", ["--created", "100", "--accept", "1.2", "--action", "0.5"]),
    ("funnel", ["--created", "100", "--accept", "0.6", "--action", "-0.1"]),
    ("delta", ["--p0", "12", "--p1", "0.09"]),
    ("retention", ["--numbers", "10", "--start-month", "1", "--churn-base", "1.5", "--churn-new", "0.1"]),
    ("sample-size", ["--p0", "0.1", "--p1", "1.1"]),
])
def test_share_outside_0_1_is_error(cmd, args):
    with pytest.raises(SystemExit) as e:
        run_calc(cmd, *args)
    assert "долей от 0 до 1" in str(e.value.code)


def test_save_goes_to_output_calc(tmp_path, monkeypatch):
    monkeypatch.setattr(calc, "ROOT", tmp_path)
    calc.main(["breakeven", "--name", "be", "--gap", "6000000", "--margin", "500",
               "--note", "тест", "--claims", "C001"])
    data = json.loads((tmp_path / "output" / "calc" / "be.json").read_text(encoding="utf-8"))
    assert data["base"]["нужно_доп_оплаченных_месяцев"] == 12000
    assert data["_meta"]["claims"] == "C001"
    assert (tmp_path / "output" / "calc" / "be.md").exists()


def test_mix_pure_structure_shift():
    r = run_calc("mix", "--base", "a=800:240;b=200:24", "--current", "a=400:120;b=600:72")
    b = r["base"]
    assert b["общая_доля_база"] == pytest.approx(0.264)
    assert b["общая_доля_сейчас"] == pytest.approx(0.192)
    assert b["вклад_долей_внутри_групп_пп"] == pytest.approx(0)
    assert b["вклад_структуры_пп"] == pytest.approx(-7.2)


def test_trial_value_and_needed_conversion():
    r = run_calc("trial-value", "--convert", "0.5", "--retain", "1", "--trial-months", "3",
                 "--horizon", "5", "--margin", "100", "--active", "1", "--free-cost", "10",
                 "--target", "0")
    b = r["base"]
    assert b["оплаченных_месяцев_на_аккаунт"] == pytest.approx(1.0)
    assert b["итог_на_аккаунт_руб"] == pytest.approx(70)       # 100 - 3*10
    assert b["нужна_конверсия_для_цели"] == pytest.approx(0.15)  # 30 / (100*2)


def test_activation_payback_simple_flow():
    # 10 аккаунтов в месяц, прирост 10 п.п., все новые загрузившие платят, удержание 1, без расходов,
    # пробный 1 мес., запуск сразу: к месяцу m когорта j даёт (m - j) оплаченных месяцев * 0.1 * 100
    r = run_calc("activation-payback", "--accounts-per-month", "10", "--upload-lift", "0.1",
                 "--pay-share", "1", "--retain", "1", "--margin", "100", "--trial-months", "1",
                 "--horizon", "3", "--investment", "250")
    b = r["base"]
    assert b["накопленно_за_горизонт_руб"] == 300   # (2 + 1 + 0) * 10 * 0.1 * 100
    assert b["окупается_в_квартале"] == 1


def test_activation_payback_lag_and_needed_lift():
    args = ["activation-payback", "--accounts-per-month", "10", "--upload-lift", "0.1", "--pay-share", "1",
            "--retain", "1", "--margin", "100", "--trial-months", "1", "--horizon", "3"]
    assert run_calc(*args, "--lag", "1")["base"]["накопленно_за_горизонт_руб"] == 100  # (1 + 0) * 100
    r = run_calc(*args, "--budgets", "600;5000")
    need = r["base"]["нужен_прирост_загрузки_пп_для_окупаемости"]
    assert need["600"] == pytest.approx(20.0, abs=0.1)
    assert need["5000"] == "не окупается даже при приросте 100 п.п."
