from __future__ import annotations

import json
import sqlite3
import zipfile
from datetime import date
from pathlib import Path

from backend.app.services.attributed_analytics import build_attributed_snapshot
from fastapi.testclient import TestClient


def _xlsx(path: Path) -> None:
    sheet = """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <sheetData>
        <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>
        <row r="2"><c r="A2"><v>45691</v></c><c r="B2"><v>20</v></c></row>
        <row r="3"><c r="A3"><v>45692</v></c><c r="B3"><v>10</v></c></row>
      </sheetData>
    </worksheet>"""
    shared = """<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <si><t>Date</t></si><si><t># Emails</t></si></sst>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        archive.writestr("xl/sharedStrings.xml", shared)


def _table(rows: list[list[str]]) -> str:
    return (
        "<w:tbl>"
        + "".join(
            "<w:tr>"
            + "".join(f"<w:tc><w:p><w:r><w:t>{cell}</w:t></w:r></w:p></w:tc>" for cell in row)
            + "</w:tr>"
            for row in rows
        )
        + "</w:tbl>"
    )


def _docx(path: Path) -> None:
    roles = [
        ["Role Family", "Apps", "HM / Team", "HM %", "Final", "Final %", "Offer"],
        ["Product Management", "10", "2", "20.0%", "1", "10.0%", "0"],
    ]
    sources = [
        ["Source", "Apps", "HM / Team", "HM %", "Final", "Final %"],
        ["Yahoo", "10", "2", "20.0%", "1", "10.0%"],
    ]
    document = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{_table(roles)}{_table(sources)}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)


def _ics(path: Path) -> None:
    path.write_text(
        """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20260805T100000
SUMMARY:Interview with Example Corp - Senior Product Manager
END:VEVENT
END:VCALENDAR
""",
        encoding="utf-8",
    )


def test_snapshot_uses_outbound_plan_account_mapping_and_calendar_attribution(
    tmp_path: Path,
) -> None:
    plan, funnel, calendar, database = (
        tmp_path / "plan.xlsx",
        tmp_path / "funnel.docx",
        tmp_path / "calendar.ics",
        tmp_path / "jobs.db",
    )
    _xlsx(plan)
    _docx(funnel)
    _ics(calendar)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE jobs (id INTEGER PRIMARY KEY, company TEXT, title TEXT, role_family TEXT)"
        )
        connection.execute(
            "INSERT INTO jobs VALUES (1, 'Example Corp', 'Senior Product Manager', '')"
        )
        connection.execute(
            """CREATE TABLE imap_message_metadata (
                provider TEXT, message_identity TEXT, subject TEXT, text_body TEXT,
                imap_internal_date TEXT, received_at TEXT
            )"""
        )
        connection.execute(
            """CREATE TABLE email_classifications (
                message_identity TEXT, classification TEXT, job_id INTEGER
            )"""
        )
        connection.execute(
            "INSERT INTO imap_message_metadata VALUES (?, ?, ?, ?, ?, ?)",
            (
                "yahoo",
                "identity",
                "Application received",
                "Role at Example Corp",
                "2026-08-05T10:00:00",
                None,
            ),
        )
        connection.execute(
            "INSERT INTO email_classifications VALUES (?, ?, ?)",
            ("identity", "APPLICATION_CONFIRMATION", 1),
        )
        connection.execute(
            "INSERT INTO imap_message_metadata VALUES (?, ?, ?, ?, ?, ?)",
            (
                "yahoo",
                "reply-identity",
                "Recruiter response",
                "Example Corp would like to speak",
                "2026-08-06T10:00:00",
                None,
            ),
        )
        connection.execute(
            "INSERT INTO email_classifications VALUES (?, ?, ?)",
            ("reply-identity", "RECRUITER_REPLY", 1),
        )
    result = build_attributed_snapshot(
        plan, funnel, calendar, database, through_date=date(2026, 8, 8)
    )
    assert result["application_activity"]["recorded_applications"] == 30
    assert result["application_activity"]["active_day_average"] == 15.0
    assert result["application_activity"]["rolling_windows"][-1]["days"] == 90
    ninety_days = result["application_activity"]["rolling_windows"][-1]
    assert ninety_days["combined_unique_applications"] == 1
    assert result["application_activity"]["monthly"][0]["change_percent"] is None
    assert result["funnel"]["applications"] == 10
    assert result["funnel"]["by_account"][0]["account"] == "solovat@yahoo.com"
    assert result["funnel"]["by_account"][0]["default_role_family"].startswith("Product")
    assert result["calendar"]["event_count"] == 1
    assert result["calendar"]["monthly"][0]["change_percent"] is None
    assert result["calendar"]["company_attributed_count"] == 1
    assert result["calendar"]["by_company"][0]["company"] == "Example Corp"
    assert result["calendar"]["by_role"][0]["role_family"] == "Product Management"
    assert result["email_evidence"]["message_count"] == 2
    assert result["email_evidence"]["company_attributed_count"] == 2
    assert result["email_evidence"]["by_account"][0]["account"] == "solovat@yahoo.com"
    assert result["email_evidence"]["unique_application_confirmation_count"] == 1
    august = result["application_activity"]["combined_monthly"][-1]
    assert august == {
        "period": "2026-08",
        "plan_applications": None,
        "linkedin_extension_applications": 0,
        "email_confirmed_applications": 1,
        "combined_unique_applications": 1,
        "combined_source": "email_confirmation",
        "change_percent": None,
        "comparison_label": "2026-07",
    }
    august_intelligence = result["application_activity"]["intelligence_monthly"][-1]
    assert august_intelligence["recruiter_replies"] == 1
    assert august_intelligence["linked_interviews"] == 0
    assert august_intelligence["calendar_interview_events"] == 1
    assert august_intelligence["response_rate"] == 100.0
    serialized = json.dumps(result)
    assert "Senior Product Manager" not in serialized


def test_attributed_endpoint_reads_ignored_snapshot(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database = isolated_app
    snapshot = {
        "snapshot_version": "attributed-analytics-v1",
        "funnel": {"applications": 10},
    }
    database.with_name("attributed_analytics.json").write_text(json.dumps(snapshot))
    response = client.get("/analytics/attributed")
    assert response.status_code == 200
    assert response.json()["available"] is True
    assert response.json()["snapshot"]["funnel"]["applications"] == 10
    dashboard_script = client.get("/static/app.js")
    assert dashboard_script.status_code == 200
    assert "MoM application change" in dashboard_script.text
    assert "Recruiter replies" in dashboard_script.text
    assert "Interview conversion" in dashboard_script.text


def test_snapshot_includes_account_scoped_mbox_confirmations(
    isolated_app: tuple[TestClient, Path], tmp_path: Path
) -> None:
    client, database = isolated_app
    message = (
        b"From careers@example.com Sat Aug 01 00:00:00 2026\n"
        b"Subject: Application confirmation\n"
        b"From: careers@example.com\n"
        b"Date: Sat, 01 Aug 2026 12:00:00 +0000\n"
        b"Message-ID: <pm-mbox@example.com>\n"
        b"Content-Type: text/plain; charset=utf-8\n\n"
        b"Thank you for applying. Position: Product Manager\n"
    )
    response = client.post(
        "/imports/mbox",
        data={"mailbox_name": "gmail", "account_namespace": "solovat@gmail.com"},
        files={"file": ("pm.mbox", message, "application/mbox")},
    )
    assert response.status_code == 200
    plan = tmp_path / "plan.xlsx"
    funnel = tmp_path / "funnel.docx"
    calendar = tmp_path / "calendar.ics"
    _xlsx(plan)
    _docx(funnel)
    _ics(calendar)

    result = build_attributed_snapshot(
        plan, funnel, calendar, database, through_date=date(2026, 8, 8)
    )

    august = result["application_activity"]["combined_monthly"][-1]
    assert august["combined_unique_applications"] == 1
    assert result["email_evidence"]["unique_application_confirmation_count"] == 1
    accounts = {row["account"] for row in result["email_evidence"]["by_account"]}
    assert "solovat@gmail.com" in accounts


def test_extension_submission_ledger_is_authoritative_for_its_recorded_month(
    tmp_path: Path,
) -> None:
    plan, funnel, calendar, runtime, ledger = (
        tmp_path / "plan.xlsx",
        tmp_path / "funnel.docx",
        tmp_path / "calendar.ics",
        tmp_path / "runtime.db",
        tmp_path / "legacy-linkedin.db",
    )
    _xlsx(plan)
    _docx(funnel)
    _ics(calendar)
    for path in (runtime, ledger):
        with sqlite3.connect(path) as connection:
            connection.execute(
                """CREATE TABLE jobs (
                    id INTEGER PRIMARY KEY, linkedin_job_id TEXT, company TEXT, title TEXT,
                    role_family TEXT, source TEXT, status TEXT, application_source TEXT,
                    first_seen_at TEXT, applied_at TEXT
                )"""
            )
            connection.execute(
                """CREATE TABLE imap_message_metadata (
                    provider TEXT, message_identity TEXT, subject TEXT, text_body TEXT,
                    imap_internal_date TEXT, received_at TEXT
                )"""
            )
            connection.execute(
                "CREATE TABLE email_classifications "
                "(message_identity TEXT, classification TEXT, job_id INTEGER)"
            )
    with sqlite3.connect(ledger) as connection:
        connection.execute(
            "INSERT INTO jobs VALUES (1, 'extension-1', 'Example', 'Product Manager', '', "
            "'linkedin', 'applied', 'linkedin_extension_legacy', '2026-08-01T09:00:00', "
            "'2026-08-01T09:00:00')"
        )
    result = build_attributed_snapshot(
        plan,
        funnel,
        calendar,
        runtime,
        through_date=date(2026, 8, 8),
        linkedin_submission_ledger_path=ledger,
    )
    august = result["application_activity"]["combined_monthly"][-1]
    assert august["linkedin_extension_applications"] == 1
    assert august["combined_unique_applications"] == 1
    assert august["combined_source"] == "linkedin_extension_ledger"
