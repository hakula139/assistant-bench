# GitHub

Conventions for commits, branches, pull requests, and issues.

## Commits and Branches

- Keep each commit to one logical change. Use the imperative mood for its title, formatted as `type(scope): description`. For substantial changes, add a body explaining the problem, rationale, and tradeoffs.
- Choose one of these types: `feat`, `fix`, `perf`, `refactor`, `docs`, `test`, `ci`, `build`, `chore`, `style`, or `revert`.
- Use the most specific scope the change touches, such as a module (`catalog`, `generate`, `jobs`), a family (`smoke-text`), or a documentation target (`AGENTS`, `guide`). Omit the scope for changes that span several areas.
- Name branches `<type>/<short-name>`, using the same types.

## Pull Requests

- Merge into `main` only with squash merges. The squash commit uses the PR title, so write the title as the commit message `main` should keep.
- Fill in `.github/pull_request_template.md`. Start the Summary with prose describing the problem and resulting behavior, then cover each part of the change in a bullet with a bold label. In the Test plan, list the checks actually run and note any verification limits.
- Add `Closes #N` for each resolved issue.
- Do not request a review from the PR author because GitHub rejects the request.
- Maintainers with triage access assign the PR to `hakula139` and add one type label:

| Commit type               | Label                            |
| ------------------------- | -------------------------------- |
| `feat`                    | `enhancement`                    |
| `fix`                     | `bug`                            |
| `perf`                    | `performance`                    |
| `refactor`                | `refactor`                       |
| `docs`                    | `documentation`                  |
| `test`                    | `test`                           |
| `ci`                      | `ci`                             |
| `build`, `chore`, `style` | `chore`                          |
| `revert`                  | The reverted change's type label |

## Review and Merge

- Review each PR once. Fix every finding, including suggestions, verify the fixes with the [verification](../../AGENTS.md#verification) block, and merge without a second review round. After a batch of related PRs merges, make one simplification pass over the merged changes.
- Agents working for the maintainer merge a reviewed PR without asking for per-PR approval, queuing an auto-merge while checks run. An agent that opens a PR leaves queuing it to the agent that reviews it.
- Keep each line of work's open PRs in a single stack, with each PR based on the one before it and no more than three unmerged at a time. Land the bottom PR before opening another, since parallel PRs based on `main` multiply the conflicts created by each merge.
- The repository has no merge queue. After each merge, run `gh pr update-branch` on queued PRs that fell behind, and send conflicting PRs back to their authors to rebase. Land ready PRs in the order that minimizes conflicts, small or foundational ones first.

## Issues

- Open one issue for each actionable outcome, and include acceptance criteria. Label it like a PR.
- Use open issues to track planned work. For a theme spanning several issues, create an `epic` parent with native sub-issues.
- If an issue depends on another landing first, record that order with a native blocked-by dependency.
- Label open design questions `design`, and close them once the decision is documented in `docs/design/`.
- Close out-of-scope requests as not planned, with the reason.
- Close an issue as completed when its PR merges, and tick each acceptance criterion the merged work satisfies. Leave an unmet criterion unchecked, and track it in a follow-up issue.
