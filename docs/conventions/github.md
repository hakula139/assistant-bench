# GitHub

Repository rules for pull requests and issues beyond the general Git workflow.

## Pull Requests

- Keep each line of work's open PRs in one stack with at most three unmerged, and land the bottom PR before opening another.
- After a batch of related PRs merges, make one simplification pass over the merged changes.
- Give each PR one review round, and merge it once every finding from that round is fixed and the required checks pass, without per-PR approval or a second review, even after a rebase.
- Commit a PR's screenshots to one directory per PR on the orphan `docs/pr-screenshots` branch, link them by commit SHA, and show only synthetic data in them.

## Issues

- Give a theme that spans several issues an `epic` parent with native sub-issues, and record an issue that must wait for another with a native blocked-by link.
- Label an open design question `design`, and close it once `docs/design/` records the decision.
