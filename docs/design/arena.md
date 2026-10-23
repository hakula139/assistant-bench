# Arena

The arena shows the owner blind pairs of outputs from a family scored by votes and records each vote. `arena/` holds the vote store (`store.py`), the pair scheduler (`scheduler.py`), the sessions and blinded pairs (`service.py`), and the JSON API with the render origin (`api.py`).

## Blinding

The vote page receives an opaque pair token, the case's checklist and the instruction its trials ran, read from the rendered task, and the two outputs, while the server keeps the mapping from each token to its systems, fingerprints, and trials. Nothing the page receives identifies a system: no name, fingerprint, timing, token count, or error text, and every unknown session, pair, or output answers with the same `not found`. Votes record their duration from the server's clock, from serving the pair to receiving the vote.

A task family with `render = "diff"` sends each solution as the diff of its `/app` against the task's own repository, which collection computes without `.git`, so no commit message, author, or date reaches the page. Every system on the family runs under the same agent, which leaves no files or trailers of its own, and the page receives no trajectory, command, turn count, test output, or reward. Paths are sorted and the context is fixed, so two diffs differ only where the solutions do. A model that names itself in a comment or a file it writes still shows through, as it can in prose.

## Sessions and Pairs

A session belongs to one family and ends once no case remains, since it shows each case at most once. Its `remaining` counts the cases it has yet to vote on, including the open pair's. The vote page keeps each family's session ID in `localStorage` and asks the arena to resume it, so the store decides its votes and which cases remain, and a session the arena no longer has gives way to a new one. Tabs that resume the same session share its open pair, so a vote or undo in one retires the pair the others show, and each follows the session's current pair. Each draw picks two systems from the family's `systems` list and one of the family's current cases that both have outputs for, then draws one sample of each at random, so every sample collects votes over time. A case whose ID leaves `cases.jsonl` loses its rendered task at the next render and its outputs at the next collection, so the votes on them leave every fit:

1. **Repeats:** once a vote is a day old, it may return with its sides swapped, at 10% odds per draw, until it has been repeated once. Agreement on repeats is the rater-consistency figure in [Ranking](ranking.md#diagnostics).
2. **Seed:** every ordered pair of systems first collects 2 votes, so each pair reaches 4 with both sides counterbalanced, taking the pair with the fewest votes first.
3. **Adaptive:** after the seed, the scheduler fits the family's board with 1,000 resamples, rooted at the most-voted system as in [Ranking](ranking.md#scores-and-intervals), and weighs each pair of systems whose intervals overlap by p(1 − p) / (n + 1), where p is the fitted preference and n the pair's votes, which favors close pairs with few votes. When no pair the session can still draw overlaps, every such pair is weighed. The order shown less often goes on the left. The scheduler refits the board when a session starts, once 5 votes have been cast or undone since the last fit, and when the board lacks a system the draw weighs, so the draws in between reuse that fit.

Only outputs that passed every gate reach the owner, and two samples with identical text are never paired, since the outputs already decide those results. `arena/resolution.py` derives them afresh from the current outputs whenever the scheduler or `bench close` fits the board, so a later collection that changes a [gate verdict](families/writing.md#gate-verdicts) changes the results with it, and the vote store holds the owner's votes alone. A failing output loses, two failing outputs count as both bad, and identical outputs tie. Each pairing of two systems' samples on a case counts 1 / (the product of the two systems' sample counts on the case) of a vote, so a case weighs at most one vote per pair of systems, and since no pane showed either side, the fit's left-side intercept leaves these results out. Outputs without a verdict, such as errored trials, take no part.

On a task family, the hidden tests are the only gate, so the owner sees only pairs of passing solutions. A task with one passing system needs no vote, since that system beats every failing one and the failing ones tie, and a task no system passes ties every pair, so `remaining` counts only the tasks where two systems' passing solutions differ.

`reconcile` in `resolution.py` maps each recorded vote to the current outputs it compared, and the scheduler, `bench close`, and undo all go through it. A vote drops out of every fit when either output has left the outputs file, as a lower `samples` or a case that left `cases.jsonl` removes it, or no longer passes its gates, and when the two outputs now settle the pair, whose settled result takes its place. A regraded task trial keeps the identity of the trial whose agent wrote its solution, so a tests-only fix keeps each vote while both solutions still pass. Undo withdraws the latest vote either way, and when its pair cannot return, the session draws a fresh one.

## Vote Store

`votes.sqlite` in the data directory holds three append-only tables, whose triggers abort any update, delete, or insert over an existing row: `sessions`, `votes` with one row per vote, and `undos`, where an undo names the vote it withdraws. Each vote records its session, case, both sides' system, fingerprint, job, and trial, the choice (`a`, `b`, `tie`, or `both_bad`), the defects tagged on each side and the note, the duration, `repeat_of`, and the time. Foreign keys are enforced, so an undo names a recorded vote. Reading the votes leaves out every undone one. The file's `user_version` holds the schema version, and the store refuses to open a file of another version.

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
