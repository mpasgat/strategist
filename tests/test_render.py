import json

from conftest import TOOLS, run

RENDER = TOOLS / "render.py"

CLAIMS = [
    {"id": "C001", "text": "Прибыль 279 млн руб.", "type": "fact", "source": "project/sources/brief.md#p1"},
    {"id": "C002", "text": "Примут 60% групп", "type": "assumption", "basis": "экспертная оценка"},
    {"id": "C003", "text": "Старая оценка", "type": "fact", "source": "project/sources/brief.md#p1",
     "status": "superseded", "reason": "новые данные", "replaced_by": "C001"},
    {"id": "C004", "text": "Через год 9000 групп", "type": "forecast", "basis": "output/calc/x.json"},
    {"id": "C005", "text": "Стоимость подключения неизвестна", "type": "unknown", "where": "маркетинг"},
]

FINAL = (
    "# Итог\n\n"
    "По допущению примут 60% групп [C002], прибыль 279 млн руб. [C001]\n"
    "Прогноз: 9000 групп [C004, C001]; цена неизвестна [C005] <!-- ok -->\n"
    "Команда: `lint [C001]`\n\n"
    "```text\nвыход [C002]\n```\n"
)


def make(project, final=FINAL, claims=CLAIMS):
    (project / "state/claims.jsonl").write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in claims) + "\n", encoding="utf-8")
    (project / "output/final.md").write_text(final, encoding="utf-8")


def render(project, *args):
    return run(RENDER, args, cwd=project, env_extra={"CLAUDE_PROJECT_DIR": str(project)})


def test_footnotes_and_references(project):
    make(project)
    r = render(project)
    assert r.returncode == 0, r.stdout + r.stderr
    out = (project / "output/final_readable.md").read_text(encoding="utf-8")
    body, refs = out.split("## Основания")
    # номера по порядку первого появления, группа остаётся группой
    assert "примут 60% групп [1], прибыль 279 млн руб. [2]" in body
    assert "9000 групп [3, 2]" in body
    assert "[4]" in body
    # пометки в тексте сохранены, служебная пометка линтера убрана
    assert "По допущению" in body and "Прогноз:" in body and "неизвестна" in body
    assert "<!-- ok -->" not in body
    # код не трогаем
    assert "`lint [C001]`" in body
    assert "выход [C002]" in body
    # раздел «Основания»: текст, тип, источник
    assert "1. Примут 60% групп. *Допущение*; основание: экспертная оценка. (C002)" in refs
    assert "2. Прибыль 279 млн руб. *Факт*; источник: project/sources/brief.md#p1. (C001)" in refs
    assert "3. Через год 9000 групп. *Прогноз*; основание: output/calc/x.json. (C004)" in refs
    assert "4. Стоимость подключения неизвестна. *Неизвестно*; где взять: маркетинг. (C005)" in refs
    assert "C003" not in out


def test_superseded_or_missing_ref_fails(project):
    make(project, final="Старое [C003], нет такого [C099]\n")
    r = render(project)
    assert r.returncode == 1
    assert "C003 отозван" in r.stderr and "C099" in r.stderr
    assert not (project / "output/final_readable.md").exists()


def test_custom_in_out(project):
    make(project)
    (project / "output/strategy.md").write_text("Прибыль [C001]\n", encoding="utf-8")
    r = render(project, "--in", "output/strategy.md")
    assert r.returncode == 0, r.stderr
    assert "Прибыль [1]" in (project / "output/strategy_readable.md").read_text(encoding="utf-8")
