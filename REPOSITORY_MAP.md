# Repository Map

```text
backend/
  app/
    api/
    services/
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
tests/
extension/
docs/
```

The default runtime database is `data/jobs.db`. `backend/jobs.db.migrated` is a temporary ignored
local rollback artifact and is not part of the repository layout committed to Git.
