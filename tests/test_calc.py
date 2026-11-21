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
