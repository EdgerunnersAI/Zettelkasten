from __future__ import annotations

from pathlib import Path

import run


def test_gunicorn_command_uses_post_fork_config(monkeypatch) -> None:
    captured = {}
    monkeypatch.delenv("ENV", raising=False)
    def _capture(cmd):
        captured["cmd"] = cmd
        return 0

    monkeypatch.setattr(run.subprocess, "call", _capture)

    assert run.main() == 0
    cmd = captured["cmd"]
    assert "--config" in cmd
    assert cmd[cmd.index("--config") + 1] == "website/gunicorn_conf.py"
    assert Path("website/gunicorn_conf.py").exists()
