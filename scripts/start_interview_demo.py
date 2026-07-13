#!/usr/bin/env python3
"""Start a sanitized Interview Pipeline demo against a disposable database."""

from __future__ import annotations

import argparse
import atexit
import importlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def build_mbox(cases: list[dict[str, str]]) -> bytes:
    messages: list[str] = []
    for index, item in enumerate(cases):
        messages.append(
            "From demo@example.com Sat Jan 01 00:00:00 2026\n"
            f"Subject: {item['subject']}\n"
            "From: Demo Recruiter <demo.recruiter@acme.example>\n"
            "Date: Thu, 01 Jan 2026 12:00:00 +0000\n"
            f"Message-ID: <interview-demo-{index}@example.invalid>\n"
            "Content-Type: text/plain; charset=utf-8\n\n"
            f"{item['body']}\n\n"
        )
    return "".join(messages).encode()


def prepare_demo() -> tuple[Path, object]:
    temporary_directory = Path(tempfile.mkdtemp(prefix="job-intelligence-interview-demo-"))
    atexit.register(shutil.rmtree, temporary_directory, ignore_errors=True)
    database = temporary_directory / "interview-demo.db"
    os.environ["JOBS_DB_PATH"] = str(database)

    from alembic import command
    from alembic.config import Config
    from fastapi.testclient import TestClient

    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    module = importlib.import_module("backend.main")
    cases = json.loads((ROOT / "tests" / "fixtures" / "interview" / "cases.json").read_text())
    with TestClient(module.app) as client:
        response = client.post(
            "/jobs/upsert",
            json={
                "linkedin_job_id": "REQ-7000",
                "title": "Sanitized Demo Role",
                "company": "Acme Demo",
            },
        )
        response.raise_for_status()
        imported = client.post(
            "/imports/mbox",
            data={"mailbox_name": "gmail"},
            files={"file": ("sanitized-interviews.mbox", build_mbox(cases), "application/mbox")},
        )
        imported.raise_for_status()
    return database, module


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--port", type=int, default=8003)
    arguments = parser.parse_args()
    database, module = prepare_demo()
    print("NON-PRODUCTION TEMPORARY INTERVIEW DEMO")
    print(f"Temporary database: {database}")
    if arguments.prepare_only:
        print("Demo preparation and isolated import completed.")
        return
    dashboard_url = f"http://127.0.0.1:{arguments.port}/"
    print(f"Dashboard: {dashboard_url}")
    print("Press Ctrl+C to stop and delete the temporary database.")
    import uvicorn

    uvicorn.run(module.app, host="127.0.0.1", port=arguments.port)


if __name__ == "__main__":
    main()
