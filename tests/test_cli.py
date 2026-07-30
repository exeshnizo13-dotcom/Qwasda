from __future__ import annotations

from qwasda import __main__


def test_version_command(capsys):
    assert __main__.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "1.5.0"


def test_version_command_uses_windows_handle_without_console(monkeypatch):
    output = []
    monkeypatch.setattr(__main__.sys, "stdout", None)
    monkeypatch.setattr(__main__, "_write_windows_stdout", lambda text: output.append(text))

    assert __main__.main(["--version"]) == 0
    assert output == ["1.5.0\n"]


def test_smoke_test_command():
    assert __main__.main(["--smoke-test"]) == 0


def test_shutdown_command_reports_signal_result(monkeypatch):
    monkeypatch.setattr(__main__, "request_shutdown", lambda: True)
    assert __main__.main(["--shutdown"]) == 0
    monkeypatch.setattr(__main__, "request_shutdown", lambda: False)
    assert __main__.main(["--shutdown"]) == 1
