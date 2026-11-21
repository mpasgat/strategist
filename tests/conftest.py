"""Общие помощники тестов. Тесты не ходят в сеть и не пишут в репозиторий: всё во временных папках."""
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"


def load_tool(name):
    """Импортирует tools/<name>.py как модуль (без запуска main)."""
    spec = importlib.util.spec_from_file_location(f"tool_{name}", TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(script, args, cwd, stdin=None, env_extra=None):
    """Запускает скрипт отдельным процессом с UTF-8 вводом-выводом."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(script), *args], cwd=str(cwd), input=stdin,
                          capture_output=True, text=True, encoding="utf-8", env=env)


@pytest.fixture
def project(tmp_path):
    """Пустая копия структуры проекта: tools/ со скриптами, project/sources, state, output."""
    (tmp_path / "tools").mkdir()
    for f in TOOLS.glob("*"):
        if f.is_file():
            shutil.copy(f, tmp_path / "tools" / f.name)
    for d in ("project/sources", "state", "output"):
        (tmp_path / d).mkdir(parents=True)
    return tmp_path
