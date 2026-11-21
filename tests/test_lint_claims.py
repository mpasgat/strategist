import json

from conftest import TOOLS, run

LINT = TOOLS / "lint_claims.py"

CLAIMS = [
    {"id": "C001", "text": "Прибыль 279 млн руб.", "type": "fact", "source": "project/sources/brief.md#p1"},
    {"id": "C002", "text": "Примут 60% групп", "type": "assumption", "basis": "экспертная оценка"},
    {"id": "C003", "text": "Старая оценка", "type": "fact", "source": "project/sources/brief.md#p1",
     "status": "superseded", "reason": "новые данные", "replaced_by": "C001"},
    {"id": "C004", "text": "Через год 9000 групп", "type": "forecast", "basis": "calc"},
]


def make(project, draft, claims=CLAIMS, step=None):
    (project / "project/sources/brief.md").write_text(
        '# brief\n\n<a id="p1"></a>\n## p1\n\nтекст\n', encoding="utf-8")
    (project / "state/claims.jsonl").write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in claims) + "\n", encoding="utf-8")
    (project / "output/strategy.md").write_text(draft, encoding="utf-8")
    if step is not None:
        (project / "state/task.json").write_text(json.dumps({"step": step}), encoding="utf-8")


def lint(project, *args, stdin=None):
    return run(LINT, args, cwd=project, stdin=stdin, env_extra={"CLAUDE_PROJECT_DIR": str(project)})


def test_clean_draft_passes(project):
    make(project, "# Стратегия\n\nПрибыль 279 млн руб. [C001]\n"
                  "По допущению примут 60% групп [C002]\nПрогноз: 9000 групп [C004]\n")
    r = lint(project, "output/strategy.md")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ОК" in r.stdout


def test_number_without_ref(project):
    make(project, "Выручка вырастет на 15% за год.\n")
    r = lint(project, "output/strategy.md")
    assert r.returncode == 1
    assert "нет ссылки [Cxxx]" in r.stdout


def test_assumption_without_label(project):
    make(project, "Примут 60% групп [C002]\n")
    r = lint(project, "output/strategy.md")
    assert r.returncode == 1
    assert "C002 это assumption" in r.stdout


def test_ref_to_superseded(project):
    make(project, "Прибыль 279 млн руб. [C003]\n")
    r = lint(project, "output/strategy.md")
    assert r.returncode == 1
    assert "C003 отозван" in r.stdout


def test_ref_to_missing_claim(project):
    make(project, "Прибыль 279 млн руб. [C099]\n")
    r = lint(project, "output/strategy.md")
    assert r.returncode == 1
    assert "несуществующий C099" in r.stdout


def test_missing_source_anchor(project):
    bad = CLAIMS + [{"id": "C005", "text": "x", "type": "fact", "source": "project/sources/brief.md#p9"}]
    make(project, "Прибыль 279 млн руб. [C001]\n", claims=bad)
    r = lint(project, "output/strategy.md")
    assert r.returncode == 1
    assert "нет метки #p9" in r.stdout


def test_missing_source_file(project):
    bad = CLAIMS + [{"id": "C005", "text": "x", "type": "fact", "source": "project/sources/none.md#p1"}]
    make(project, "", claims=bad)
    r = lint(project)
    assert r.returncode == 1
    assert "файл источника не найден" in r.stdout


def test_not_numbers_are_ignored(project):
    draft = "\n".join([
        "# План на 2026 год",
        "В 2026 и 2027 году работаем без изменений.",
        "Решение в Q1–Q4, отчёт во 2 квартале.",
        "На шаге 5 человек выбирает, затем шаг 6.",
        "Мы сравнили 3 варианта, вариант 2 лучше.",
        "Это СТОП 2, см. раздел 4 и пункт 3.",
        "1. Первый пункт списка без чисел",
        "Число в строке с исключением 42 <!-- ok -->",
        "```",
        "код 123 не проверяется",
        "```",
        "| a | b |",
        "|---|---|",
    ]) + "\n"
    make(project, draft)
    r = lint(project, "output/strategy.md")
    assert r.returncode == 0, r.stdout


def test_hook_skips_before_step_5(project):
    make(project, "Выручка вырастет на 15%.\n", step=4)
    r = lint(project, "--hook", stdin="{}")
    assert r.returncode == 0, r.stdout + r.stderr


def test_hook_skips_without_task_json(project):
    make(project, "Выручка вырастет на 15%.\n")
    r = lint(project, "--hook", stdin="{}")
    assert r.returncode == 0


def test_hook_skips_when_stop_hook_active(project):
    make(project, "Выручка вырастет на 15%.\n", step=5)
    r = lint(project, "--hook", stdin=json.dumps({"stop_hook_active": True}))
    assert r.returncode == 0, r.stdout + r.stderr


def test_hook_blocks_from_step_5(project):
    make(project, "Выручка вырастет на 15%.\n", step=5)
    r = lint(project, "--hook", stdin=json.dumps({"stop_hook_active": False}))
    assert r.returncode == 2
    assert "нет ссылки" in r.stderr


def test_hook_reads_stdin_as_utf8_with_cyrillic_path(project):
    """stop_hook_active распознаётся, даже если в stdin кириллица, а локаль процесса cp1251."""
    make(project, "Выручка вырастет на 15% за год.\n", step=5)
    stdin = json.dumps({"stop_hook_active": True,
                        "transcript_path": "C:/Users/Иван/Документы/сессия.jsonl"}, ensure_ascii=False)
    r = run(LINT, ["--hook"], cwd=project, stdin=stdin,
            env_extra={"CLAUDE_PROJECT_DIR": str(project), "PYTHONIOENCODING": "cp1251", "PYTHONUTF8": "0"})
    assert r.returncode == 0, r.stdout + r.stderr


def test_root_option_checks_demo_folder(project, tmp_path):
    """--root проверяет демо в своей папке: claims, черновик и источники относительно неё."""
    demo = project / "examples" / "demo"
    for d in ("project/sources", "state", "output"):
        (demo / d).mkdir(parents=True)
    make(demo, "Прибыль 279 млн руб. [C001]\nПо допущению примут 60% групп [C002]\nПрогноз: 9000 групп [C004]\n")
    r = run(LINT, ["--root", "examples/demo", "output/strategy.md"], cwd=project)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "черновиков: 1" in r.stdout
