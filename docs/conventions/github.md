# GitHub

Repository rules for pull requests and issues beyond the general Git workflow.

## Pull Requests

- Keep each line of work's open PRs in one stack with at most three unmerged, and land the bottom PR before opening another.
- After a batch of related PRs merges, make one simplification pass over the merged changes.
- Merge a PR once its review reports no remaining findings and the required checks pass, without per-PR approval.

## Issues

- Give a theme that spans several issues an `epic` parent with native sub-issues, and record an issue that must wait for another with a native blocked-by link.
- Label an open design question `design`, and close it once `docs/design/` records the decision.
