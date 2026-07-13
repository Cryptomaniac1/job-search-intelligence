# Database Design
Entities:
- applications
- companies
- recruiters
- emails
- interview_events
- resumes
- job_descriptions
Relationships:
Application belongs to Company.
Recruiter belongs to Company.
Interview belongs to Application.

Current Sprint 7 foundation: additive `interviews` aggregates link to legacy `jobs`, while
immutable `interview_events` preserve source-message and extraction evidence. This is an
incremental implementation, not the complete target Application model.
