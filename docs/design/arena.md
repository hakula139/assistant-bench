# Arena

The arena shows blind pairs of outputs from a family scored by votes and records each choice. The [module map](architecture.md#module-map) identifies its implementation boundaries.

## Blinding

The vote page receives an opaque pair token, the current case's checklist and instruction, and the two outputs. Collection admits only trials matching that source. The server keeps the mapping from each token to its systems, fingerprints, and trials. Nothing the page receives identifies a system: no name, fingerprint, timing, token count, or error text, and every unknown session, pair, or output answers with the same `not found`. Votes record their duration from the server's clock, from serving the pair to receiving the vote.

A task family with `render = "diff"` sends each solution as the diff of its `/app` against the task's repository. Collection excludes `.git` and Python tool caches. Every system runs under the same agent, and the page receives no commit metadata, trajectory, command, turn count, test output or reward. Paths are sorted and context is fixed.

A model can still identify itself in a comment or file it writes, as it can in prose.

## Sessions and Pairs

A session belongs to one family, shows each eligible case at most once and ends when none remain. Eligibility requires passing, different outputs from two systems. `remaining` counts eligible cases still awaiting a choice, including the open pair's.

The vote page keeps each family's session ID in `localStorage` and asks the arena to resume it. The store determines recorded choices and remaining cases. An unknown session gives way to a new one.

Tabs that resume the same session share its open pair, so a vote or undo in one retires the pair the others show, and each follows the session's current pair.

Each draw picks two systems from the family's `systems` list and one of the family's current cases that both have outputs for, then draws one sample of each at random, so every sample collects votes over time. The scheduler chooses pairs in this order:

1. **Repeats:** once a vote is a day old, it may return with its sides swapped, at 10% odds per draw, until it has been repeated once. Agreement on repeats is the rater-consistency figure in [Ranking](ranking.md#counts-and-diagnostics).
2. **Seed:** every ordered pair of systems first collects 2 votes, so each pair reaches 4 with both sides counterbalanced, taking the pair with the fewest votes first.
3. **Adaptive:** after the seed, the scheduler fits the family's point scores, rooted at the most-voted system as in [Ranking](ranking.md#point-scores). It weighs eligible pairs by p(1 − p) / (n + 1), where p is the fitted preference and n the pair's votes, favoring close pairs with few votes. The order shown less often goes on the left.

The scheduler refits at session start, after 5 choices have been cast or undone, and when the board lacks a system the draw weighs. Other draws reuse the fit. These points guide acquisition and supply no population confidence claim.

Only outputs that passed every gate reach the owner, and two samples with identical text are never paired, since the outputs already decide those results. `arena/resolution.py` derives them afresh from the current outputs whenever the scheduler or `bench close` fits the board, so a later collection that changes a [gate verdict](families/writing.md#gate-verdicts) changes the results with it, and the vote store holds the owner's votes alone.

A failing output loses, two failing outputs count as both bad, and identical outputs tie. Each pairing of two systems' samples on a case counts 1 / (the product of the two systems' sample counts on the case) of a vote, so a case weighs at most one vote per pair of systems, and since no pane showed either side, the fit's left-side intercept leaves these results out. Outputs without a verdict, such as ungraded trials, take no part.

On a task family, the hidden tests are the only gate, so the owner sees only pairs of passing solutions. A task with one passing system needs no vote, since that system beats every failing one and the failing ones tie, and a task no system passes ties every pair, so `remaining` counts only the tasks where two systems' passing solutions differ.

`reconcile` in `resolution.py` maps each recorded vote to the current outputs it compared, and the scheduler, `bench close`, and undo all go through it. A vote drops out of every fit when either output has left the outputs file, as a lower `samples` or a case that left `cases.jsonl` removes it, or no longer passes its gates, and when the two outputs now settle the pair, whose settled result takes its place. A regraded task trial keeps the identity of the trial whose agent wrote its solution, so a tests-only fix keeps each vote while both solutions still pass.

Undo withdraws the latest vote either way, and when its pair cannot return, the session draws a fresh one.

## Vote Store

`votes.sqlite` in the data directory holds append-only `sessions`, `votes` and `undos` tables. Triggers reject updates, deletes and replacement inserts. Foreign keys ensure an undo names a recorded choice, and reads exclude withdrawn choices.

Votes record the session, case, both output identities, choice, defect tags, note, duration, repeat link and time. The file's `user_version` holds the schema version, and the store refuses to open another version.

## Vote API

`uv run bench serve` builds the vote page with `BENCH_LOCAL=1` and serves it from `web/dist/` with the API on `http://127.0.0.1:8790`, for every family scored by votes that has outputs:

| Route                          | Does                                                                                     |
| ------------------------------ | ---------------------------------------------------------------------------------------- |
| `GET /api/families`            | Lists the families with outputs, each as `{"name", "aspect", "display_name", "defects"}` |
| `POST /api/sessions`           | Starts a session on `{"family": ...}` and returns it with its first pair                 |
| `POST /api/sessions/{id}/pair` | Returns the session's open pair, drawing one if none is open                             |
| `POST /api/pairs/{token}/vote` | Records `{"choice", "defects", "note"}` and returns the session's next pair              |
| `POST /api/sessions/{id}/undo` | Withdraws the session's latest owner vote and shows its pair again                       |

`TrustedHostMiddleware` admits only the `127.0.0.1` host, and every POST must carry the app's own `Origin`, so another page or a DNS-rebound host cannot vote. `defects` maps `a` and `b` to lists drawn from the family's `defects`, and a vote that tags another issue answers 422 and leaves its pair open. A note holds at most 500 characters and never leaves the store, since it may quote a private case. The app serves no API docs, whose page would load scripts from a CDN with access to the API.

The vote page is `web/dist/vote/index.html`, and `bench serve` stops when the build leaves none. Every file served from `vote/` carries the vote page's policy, decided on the path after normalization, so a URL that doubles or percent-encodes a slash carries it too. The policy loads scripts, styles, fonts, images, and API calls from the app alone and frames only the render origin:

```text
Content-Security-Policy: default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline';
  font-src 'self'; img-src 'self'; connect-src 'self'; frame-src http://127.0.0.1:8791; base-uri 'none';
  form-action 'none'; frame-ancestors 'none'
```

## Render Origin

A family with `render = "html"` sends each output as a URL on a second origin, `http://127.0.0.1:8791`, which the vote page loads in `<iframe sandbox="allow-scripts">`. The origin answers with the output under this policy:

```text
Content-Security-Policy: sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline';
  style-src 'unsafe-inline'; img-src data:; connect-src 'none'; frame-ancestors http://127.0.0.1:8790
```

Outputs load by URL because a `srcdoc` frame inherits the vote page's policy. An output's URL stops working once its pair is voted on or undone.
