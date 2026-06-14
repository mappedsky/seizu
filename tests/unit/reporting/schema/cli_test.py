"""Tests for reporting.schema.cli (schema export command)."""

from click.testing import CliRunner

from reporting.schema.cli import schema_cli


def test_export_prints_schema_to_stdout():
    runner = CliRunner()
    result = runner.invoke(schema_cli, ["export"])
    assert result.exit_code == 0
    assert '"title"' in result.output


def test_export_writes_schema_to_file(tmp_path):
    runner = CliRunner()
    outfile = str(tmp_path / "schema.json")
    result = runner.invoke(schema_cli, ["export", "--output-file", outfile])
    assert result.exit_code == 0
    with open(outfile) as f:
        content = f.read()
    assert '"title"' in content
