import json
from pathlib import Path

import jsonschema

from lark.report.contract import Report, json_schema

EXAMPLE = Path(__file__).parents[1] / "docs" / "report-contract" / "example.json"


def test_schema_file_is_current():
    path = Path(__file__).parents[1] / "docs" / "report-contract" / "v1.schema.json"
    assert json.loads(path.read_text()) == json_schema(), "run `lark contract export`"


def test_example_validates():
    data = json.loads(EXAMPLE.read_text())
    jsonschema.validate(data, json_schema())
    Report.model_validate(data)


def test_quiet_day_is_valid():
    r = Report(
        report_id="daily-2026-10-09", kind="daily", title="Lark daily", generated_at="2026-10-09T12:00:00Z",
        period_start="2026-10-08", period_end="2026-10-09", summary="Nothing cleared the bar today.",
        quiet_day_reason="No candidate matched an active profile above the threshold.",
    )
    jsonschema.validate(r.model_dump(mode="json"), json_schema())
