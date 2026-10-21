# Arena

The arena shows the owner blind pairs of outputs from a family scored by votes and records each vote. `arena/` holds the vote store (`store.py`), the pair scheduler (`scheduler.py`), the sessions and blinded pairs (`service.py`), and the JSON API with the render origin (`api.py`).

## Blinding

The vote page receives an opaque pair token, the case instruction and checklist, and the two outputs, while the server keeps the mapping from each token to its systems, fingerprints, and trials. Nothing the page receives identifies a system: no name, fingerprint, timing, token count, or error text, and every unknown session, pair, or output answers with the same `not found`. Votes record their duration from the server's clock, from serving the pair to receiving the vote.

## Sessions and Pairs

A session belongs to one family and ends after 30 owner votes or once no case remains, and it shows each case at most once. Its `size` is the number of votes it can still reach. Each draw picks two systems from the family's `systems` list and one case both have outputs for, then draws one sample of each at random, so every sample collects votes over time:

1. **Repeats:** once a vote is a day old, it may return with its sides swapped, at 10% odds per draw, until it has been repeated once. Agreement on repeats is the rater-consistency figure in [Ranking](ranking.md#diagnostics).
2. **Seed:** every ordered pair of systems first collects 2 votes, so each pair reaches 4 with both sides counterbalanced, taking the pair with the fewest votes first.
3. **Adaptive:** after the seed, the scheduler refits the board with 1,000 resamples and weighs each pair of systems whose intervals overlap by p(1 − p) / (n + 1), where p is the fitted preference and n the pair's votes, which favors close pairs with few votes. A board whose intervals all separate weighs every pair. The order shown less often goes on the left.

Only outputs that passed every gate reach the owner, and two samples with identical text are never paired, since the outputs already decide those results. `arena/resolution.py` derives them afresh from the current outputs whenever the scheduler or `bench close` fits the board, so a re-ingest that changes a [gate verdict](families/prose.md#gate-verdicts) changes the results with it, and the vote store holds the owner's votes alone. A failing output loses, two failing outputs count as both bad, and identical outputs tie. Each pairing of two systems' samples on a case counts 1 / (samples × samples) of a vote, so a case weighs at most one vote per pair of systems, split evenly across both sides to stay out of the position term. Outputs without a verdict, such as errored trials, take no part.

## Vote Store

`votes.sqlite` in the data directory holds three append-only tables, whose triggers abort any update or delete: `sessions`, `votes` with one row per vote, and `undos`, where an undo names the vote it withdraws. Each vote records its session, case, both sides' system, fingerprint, job, and trial, the choice (`a`, `b`, `tie`, or `both_bad`), the flags and note, the duration, `repeat_of`, and the time. Reading the votes leaves out every undone one.

## Vote API

`uv run bench serve` builds the vote page with `BENCH_LOCAL=1` and serves it from `web/dist/` with the API on `http://127.0.0.1:8790`, for every family scored by votes that has outputs:

| Route                          | Does                                                                      |
| ------------------------------ | ------------------------------------------------------------------------- |
| `GET /api/families`            | Lists the families with outputs                                           |
| `POST /api/sessions`           | Starts a session on `{"family": ...}` and returns it with its first pair  |
| `POST /api/sessions/{id}/pair` | Returns the session's open pair, drawing one if none is open              |
| `POST /api/pairs/{token}/vote` | Records `{"choice", "flags", "note"}` and returns the session's next pair |
| `POST /api/sessions/{id}/undo` | Withdraws the session's latest owner vote and shows its pair again        |

`TrustedHostMiddleware` admits only the `127.0.0.1` host, and every POST must carry the app's own `Origin`, so another page or a DNS-rebound host cannot vote. Flags are `meaning`, `punctuation`, `register`, `script`, and `length`, and a note holds at most 500 characters.

The vote page is `web/dist/vote/index.html`, and `bench serve` stops when the build leaves none. Everything under `/vote/` carries the vote page's policy, which loads scripts, styles, fonts, images, and API calls from the app alone and frames only the render origin:

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
