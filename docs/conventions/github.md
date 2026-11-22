# GitHub

Repository rules for pull requests and issues beyond the general Git workflow.

## Pull Requests

- After a batch of related PRs merges, make one simplification pass over the merged changes.
- Attach screenshots directly to the PR and show only synthetic data in them.

## Issues

- Give a theme that spans several issues an `epic` parent with native sub-issues, and record an issue that must wait for another with a native blocked-by link.
- Label an open design question `design`, and close it once `docs/design/` records the decision.
- Name a milestone for a planned release or delivery outcome. State what will count as complete in its description and assign only issues needed to reach it. A blocked issue still belongs if it gates that outcome.

## Project

When the maintainer selects an issue, add it to the `Assistant Bench` project in `hakulab` if needed. Use `Ready` for unblocked, unclaimed work. Check dependencies, linked PRs and active worktrees or sessions before marking an item `Ready` or taking it.

Set the item to `In Progress` when you start. On handoff, return it to `Ready` if someone can continue or to `Todo` if it must wait. Record the blocker on the issue. If you lack project access, include the pending status change in the handoff.

Close an issue when its acceptance criteria are met. Closing it sets its status to `Done`. Manually setting `Done` can also close the issue.
