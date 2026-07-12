# PRODUCT_REQUIREMENTS.md

Version: 1.0
Status: Living Document
Owner: Rafael Soultanov

---

# Job Search Intelligence

AI-powered Career Operating System

---

# Table of Contents

1. Vision
2. Product Goals
3. Success Metrics
4. User Personas
5. User Stories
6. Functional Requirements
7. Non-functional Requirements
8. System Architecture
9. Data Model
10. AI Components
11. Email Intelligence
12. Recruiter CRM
13. Job Tracking
14. Resume Intelligence
15. Interview Intelligence
16. Calendar Integration
17. Browser Extension
18. LinkedIn Automation
19. Dashboard
20. Analytics
21. Notifications
22. Security
23. Deployment
24. Testing
25. Roadmap
26. Future Ideas

---

# 1. Vision

Build the world's most intelligent personal career operating system.

Unlike spreadsheets or ATS tools, Job Search Intelligence continuously learns from:

• Email
• Calendar
• Recruiters
• Interviews
• Resumes
• Job descriptions
• Company history

The system becomes the permanent memory of a professional career.

---

# 2. Product Goals

Primary

• eliminate manual tracking
• improve recruiter response rate
• increase interview conversion
• identify strongest job families
• automate repetitive work

Secondary

• resume optimization
• interview preparation
• salary analytics
• networking assistant

---

# 3. Success Metrics

Applications tracked

Target
100%

Interview classification accuracy

Target
>98%

Duplicate detection

Target
>99%

Email sync latency

<30 seconds

Dashboard loading

<2 seconds

Classification confidence

>95%

---

# 4. Personas

Persona 1

Experienced executive

Characteristics

3000+ applications

multiple resumes

multiple industries

multiple email accounts

Needs

automation

analytics

historical memory

---

Persona 2

Recruiter

Needs

history

communication

candidate timeline

---

Persona 3

AI Agent (Codex)

Needs

clear documentation

stable APIs

predictable folder structure

decision history

---

# 5. User Stories

US-001

As a job seeker

I want recruiter emails automatically classified

So that I never update spreadsheets.

---

US-002

As a job seeker

I want every interview automatically detected

So my pipeline stays accurate.

---

US-003

As a user

I want one timeline per company

So I understand every interaction.

---

US-004

As a user

I want AI recommendations

So I know where to spend my effort.

---

# 6. Functional Requirements

FR-001

Sync Gmail

Priority

Critical

---

FR-002

Sync Yahoo

Critical

---

FR-003

Sync Hotmail

Critical

---

FR-004

Detect

application

recruiter outreach

screen

technical

manager

onsite

offer

rejection

withdrawal

---

FR-005

Company timeline

Every interaction.

---

FR-006

Recruiter CRM

Each recruiter has

history

notes

companies

response time

last contact

relationships

---

FR-007

Interview Pipeline

Application

↓

Recruiter

↓

Hiring Manager

↓

Technical

↓

Panel

↓

Onsite

↓

Final

↓

Offer

---

FR-008

Resume Library

Multiple resumes

Versioning

Tags

Industries

Matching score

---

FR-009

Job Description Storage

Store

HTML

PDF

text

parsed requirements

skills

salary

location

---

# 7. Non-functional Requirements

Python 3.12

FastAPI

SQLite

REST APIs

Type hints

100% reproducible

Cross-platform

Offline capable

---

# 8. Architecture

Email Providers

↓

Synchronization

↓

Classification Engine

↓

Database

↓

FastAPI

↓

Browser Extension

↓

Dashboard

↓

AI Assistant

---

# 9. Database

Applications

Companies

Recruiters

Emails

Threads

Interviews

Resumes

JobDescriptions

CalendarEvents

Skills

Contacts

Notes

Attachments

---

# 10. AI Components

Email classifier

Resume scorer

Interview summarizer

Company summarizer

Recruiter relationship analyzer

Job fit score

Application priority

Offer evaluator

Salary estimator

Interview coach

---

# 11. Email Intelligence

Supported

Gmail

Yahoo

Hotmail

Future

Outlook

Exchange

Features

Deduplication

Thread reconstruction

Attachment parsing

Confidence score

Semantic search

---

# 12. Recruiter CRM

Store

Company

LinkedIn

Title

Email

Phone

Timezone

Notes

Response latency

Relationship score

Follow-up reminders

Interaction history

---

# 13. Job Tracking

Company

Role

Salary

Location

Resume used

Date applied

Status

Priority

Recruiter

Source

Referral

Notes

Probability

---

# 14. Resume Intelligence

Multiple resume versions

Automatic recommendation

Gap analysis

Keyword comparison

ATS optimization

Version history

---

# 15. Interview Intelligence

Interview summaries

Questions

Weak areas

Strengths

Follow-up emails

Preparation checklists

Performance history

---

# 16. Calendar Integration

Google Calendar

Apple Calendar

Outlook

Automatic interview detection

Conflict detection

Availability analytics

---

# 17. Browser Extension

LinkedIn

Greenhouse

Lever

Ashby

Workday

SmartRecruiters

Features

Auto-save jobs

Competition estimator

Resume recommendation

Company history

Recruiter history

One-click apply tracking

---

# 18. LinkedIn Automation

Detect

Applicants

Salary

Skills

Hiring manager

Recruiter

Auto-save

Priority scoring

Reminders

---

# 19. Dashboard

Overview

Applications

Interviews

Recruiters

Companies

Offers

Response rate

Conversion

Heat maps

Role analytics

Timeline

---

# 20. Analytics

Applications/week

Interviews/week

Offers

Conversion

Company ranking

Resume effectiveness

Recruiter effectiveness

Average hiring time

Role success rate

Source effectiveness

---

# 21. Notifications

Interview tomorrow

Follow-up due

Recruiter replied

Offer received

Resume recommendation

New matching jobs

---

# 22. Security

OAuth

Encrypted credentials

No plain-text passwords

Audit logs

Backups

Role-based access (future)

---

# 23. Deployment

Local

Docker

Cloud

GitHub Actions

Automatic backups

---

# 24. Testing

Unit tests

Integration tests

Email fixtures

Mock APIs

Regression suite

Performance tests

---

# 25. Roadmap

Version 1

Email sync

Classification

Dashboard

Recruiter CRM

Version 2

Browser extension

AI recommendations

Resume intelligence

Version 3

Networking assistant

Mobile app

Voice assistant

Autonomous job search agent

---

# 26. Long-term Vision

Become the operating system for an entire professional career.

Support:

Career transitions

Networking

Mentorship

Promotion tracking

Performance reviews

Compensation analysis

Personal knowledge management

Ultimately, the platform should function as an AI Chief of Staff for career management, preserving every relevant interaction, document, and decision over the course of decades.