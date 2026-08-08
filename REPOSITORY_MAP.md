# Repository Map

```text
backend/
  app/
    api/
    services/
      yahoo_imap.py       # TLS-only read-only transport and MIME normalization
      yahoo_live_sync.py  # offline production gate and post-sync evidence
      yahoo_incident.py   # read-only incident verification and recovery scoping
      oauth_imap.py       # Gmail/Hotmail OAuth settings and XOAUTH2 authentication
      provider_live_sync.py # offline Gmail/Hotmail production approval gate
      imap_checkpoint.py  # UID/UIDVALIDITY checkpoint persistence
      sync_status.py      # credential-safe provider status summaries
    models/
    schemas/
    database/
    utils/
  main.py
  static/

data/
  README.md
  .gitkeep
  jobs.db                 # local runtime data; ignored

backups/
  .gitkeep                # directory retained; contents ignored

migrations/
scripts/
  import_historical_interviews.py # protected Gmail/Hotmail/Yahoo interview replay
  rehearse_historical_interviews.py # copy-only two-pass replay and evidence workflow
  start_interview_demo.py # disposable sanitized Interview Pipeline dashboard
  sync_yahoo_imap.py      # previews, gated production sync, and evidence reporting
  sync_oauth_imap.py      # shared Gmail/Hotmail OAuth IMAP operator command
  analyze_yahoo_incident.py # offline incident analysis and disposable rollback rehearsal
  recover_yahoo_incident.py # approval-gated five-UID recovery; requires separate approval
tests/
  fixtures/classification/ # canonical cases and Version 1 reviewed benchmark
  fixtures/interview/     # sanitized deterministic extraction cases
  fixtures/yahoo_incident/ # sanitized 94/1/5 incident shape
extension/
docs/
```

The default runtime database is `data/jobs.db`. `backend/jobs.db.migrated` is a temporary ignored
local rollback artifact and is not part of the repository layout committed to Git.

Historical replay inputs are optional per provider. Gmail and Hotmail accept MBOX files. Yahoo
requires raw-message JSON with subject, sender, and body; structured Yahoo opportunity data is not
a compatible replay source.
