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

Install:
1. Remove v1.6.
2. Unzip this package.
3. Load unpacked and select this folder.
4. Reload LinkedIn search results.
5. Start the backend on port 8002.
6. Run a new scan.
