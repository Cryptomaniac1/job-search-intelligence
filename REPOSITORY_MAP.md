# Repository Map

```text
backend/
  app/
    api/
    services/
      yahoo_imap.py       # TLS-only read-only transport and MIME normalization
      imap_checkpoint.py  # UID/UIDVALIDITY checkpoint persistence
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
  sync_yahoo_imap.py      # folder listing, dry-run, and protected temporary sync
tests/
  fixtures/interview/     # sanitized deterministic extraction cases
extension/
docs/
```

The default runtime database is `data/jobs.db`. `backend/jobs.db.migrated` is a temporary ignored
local rollback artifact and is not part of the repository layout committed to Git.

Historical replay inputs are optional per provider. Gmail and Hotmail accept MBOX files. Yahoo
requires raw-message JSON with subject, sender, and body; structured Yahoo opportunity data is not
a compatible replay source.
