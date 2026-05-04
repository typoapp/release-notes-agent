from click.testing import CliRunner

from releasenotes.cli import main


def test_init_writes_starter_config():
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main, ["init"])
        assert result.exit_code == 0
        with open("releasenotes.yaml") as handle:
            content = handle.read()
        assert "llm:" in content
        assert "ingestion:" in content
