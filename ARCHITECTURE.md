# Architecture

                Gmail
                  │
                  │
              Yahoo IMAP
                  │
                  │
             Hotmail IMAP
                  │
                  ▼
          Synchronization Engine
                  │
                  ▼
         Email Classification
                  │
                  ▼
              SQLite
                  │
          ┌───────┴────────┐
          ▼                ▼
     FastAPI          Recruiter CRM
          │                │
          └───────┬────────┘
                  ▼
          Browser Extension
                  │
                  ▼
             User Interface