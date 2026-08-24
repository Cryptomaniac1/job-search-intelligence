# Job Intelligence Scanner v1.7

This version fixes two extraction defects in v1.6:

1. The scanner was reading the entire LinkedIn page instead of the selected
   right-hand job pane. That caused every dashboard row to use text such as
   "Are these results helpful?" and the search-preferences heading.

2. The scanner did not reliably wait for each selected job title to appear in
   the right-hand pane before extracting the applicant count.

v1.7:
- identifies each left card through its stable Dismiss aria-label
- clicks the title element inside that exact card
- waits until the right pane displays the same job title
- isolates the right-pane ancestor containing that title and applicant text
- extracts applicant count only from that selected job
- uses card metadata as a fallback for title, company, location, salary, and age

v1.8:
- adds **I applied — record selected job** for a job already selected in LinkedIn
- records a user-confirmed submission once in the local application ledger
- never infers an application from scanning, email, or a LinkedIn page view

Install:
1. Remove v1.6.
2. Unzip this package.
3. Load unpacked and select this folder.
4. Reload LinkedIn search results.
5. Start the backend on the URL configured in the extension popup (normally port 8000).
6. Run a new scan.
