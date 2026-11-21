from scraper.cli import main


def test_status_runs(capsys):
    assert main(["status"]) == 0
    assert "proxies configured" in capsys.readouterr().out


def test_unknown_source_is_reported(capsys):
    code = main(
        ["one", "--source", "nope", "--from", "volgograd", "--to", "moskva", "--date", "2026-12-05"]
    )
    assert code == 2
    assert "no adapter" in capsys.readouterr().err
