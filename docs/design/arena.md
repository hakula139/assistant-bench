# Arena

The arena shows blind pairs of outputs from a family scored by votes and records each choice. The [module map](architecture.md#module-map) identifies the `bench` side's implementation boundaries, and `web/worker/` holds the voting Worker that [TypeScript](../conventions/typescript.md#the-voting-worker) describes.

## Publication and Closing

The `bench` process publishes outputs to a vote store and reads votes back through Cloudflare's D1 REST API, one statement per request. `web/wrangler.toml` names the account and each target's database, and the token comes from the file that `BENCH_D1_TOKEN_FILE` in `.env` names.

```bash
uv run bench arena prepare writing-zh
uv run bench arena publish production <publication>.json
uv run bench arena status production writing-zh
uv run bench close writing-zh
```

Each case votes in exactly one store. Production votes on the public and synthetic cases of a family that sets `public_voting`, and every other case is voted locally. `prepare` requires `public_voting`, collects the family's current outputs, keeps the listed systems' outputs on the production cases, and writes `publications/<family>/<id>.json` in the data directory with a Markdown listing beside it. The listing shows each case's instruction and checklist, each passing output's text or diff under its system name, and each failing or ungraded output's system and gates. Reviewing the listing and publishing that exact file approves it.

- **Identities.** The publication ID digests the file's content without the ID, so `publish` refuses an edited file. A case's key digests its instruction and checklist, and an output's key digests its family, job and trial, so republishing unchanged outputs keeps their keys and the votes on them.
- **Content.** Only passing outputs carry content, since only they can form a voted pair. Every output carries its verdict, length and content digest, which settle the comparisons no one votes on.
- **Publishing** refuses a private case before anything leaves the machine. It inserts the cases and outputs idempotently, then activates the publication for its family only once every row is staged, so an interrupted run completes on a rerun and an identical file changes nothing.

`bench close` for a votes family takes a cut from production, saves it as `votes/<family>/<taken-at>.json` in the data directory, and closes the round from that file. `--snapshot <path>` recloses a saved file without the store.

- **A cut** holds the largest vote ID older than the ten-minute undo window, or 0 when no vote is, the active publication without content, the counted votes up to that ID and the number of excluded ones. It carries no network tags.
- **Counting.** A vote counts when both of its outputs are still in the active publication and still pass, and when their content differs. Every other vote, and every excluded one, counts in `dropped_votes`. Actors are prefixed with their store, so the actor cap and `voters` count each browser once.
- **Settlements** come from the publication's verdicts and content digests by the rule in [Pairs and Skips](#pairs-and-skips).
- **Measurements.** Latency, tokens and cost come from the collected `outputs/<family>.jsonl`, matched by output key. An output missing from the file contributes null.
- **The report** names its evidence by each store's cutoff and the counted votes up to it, null for a store closing did not read, without any digest of private content. It anchors on the reference the publication froze.

## Blinding

The browser receives the approved instruction and checklist, the case's category, the two outputs' text or diff and a random pair ID. System names, fingerprints, output keys, timings and per-system counts never leave the Worker, and its error responses carry only a fixed code. Output text can still identify its author, so reviewing a publication's listing must check the exact text the page shows.

## Pairs and Skips

Balanced coverage chooses what a voter compares, and a keyed per-voter order breaks every tie, so no skip, reload, undo or retry can draw again.

- **Candidates.** For a browser and family, a candidate is a case of the active publication and an unordered system pair where both systems have a passing output on the case, some pair of those outputs differs in content, and the browser has no vote on that comparison.
- **Keyed rank.** Each rank is HMAC-SHA-256 under `ACTOR_KEY` over the browser, the family and the item, which gives each voter a fixed order the voter can neither predict nor change.
- **Draw.** The candidate system pair with the fewest counted votes in the family wins, ties going to the lowest rank. Within it, the case with the lowest rank wins, then the distinct-content sample pair with the lowest rank, whose lowest bit orders the panes.
- **Pending pair.** Each browser has one per family. `GET /api/pair` returns it while its publication is active, so a reload or a second tab sees the same pair. It goes when voted, skipped or replaced by a new publication, and the daily cron deletes one left idle for 30 days.
- **Skip** keeps the system pair and moves to the next case in the voter's order for it, wrapping around, under a new pair ID, so a late vote on the skipped ID is stale. With no other case, the pair stays. A skip records nothing else, and coverage counts only votes.

The system pair is fixed before the voter sees anything, and only a vote releases it, so skipping never reaches a pair holding a recognized system. Undoing and voting again leaves every count where it was, so the next draw repeats.

Gate failures and identical outputs settle comparisons without a browser vote. A failing output loses, two failing outputs count as both bad, and identical outputs tie. Every pairing of two systems' samples on a case carries weight 1 / (the product of their sample counts), so one case contributes at most one settled vote per pair of systems. Settled comparisons have no display side.

A publication freezes these output facts, and only a later publication changes them. An undone or excluded vote stays out of the fit.

## Vote Store

Production D1 is the sole source of real votes, including votes cast from a MacBook. Staging uses a separate preview D1 for deployed validation.

`web/wrangler.toml` names each target's database, whose tables `web/migrations/` defines, and the voting Worker in `web/worker/` serves each target's vote API from it.

## Moderation

Each vote row keeps its browser's actor ID, a keyed tag of its network and `elapsed_ms`, the time the voting Worker measured from issuing the pair to recording the vote. A tag is a digest of the network address under a key only the Worker holds, so `bench arena votes` lists tags as opaque handles and the owner selects one by copying it. The listing reads a family's votes in keyset pages and aggregates them in the `bench` process, which keeps the medians out of SQL.

`exclude` and `restore` are each one `UPDATE` of the `exclusion` column, selected by family and by one tag, one actor or a list of vote IDs bound as a single JSON parameter. A null `exclusion` means counted. Excluding skips votes already excluded, so each keeps its first reason, and the reported count is the votes that changed. The update covers only the votes recorded when it runs and blocks nothing, so later votes from the same tag or actor count until excluded too. An excluded vote stays in the store and keeps its comparison in `votes_comparison`, so its browser cannot vote that comparison again, while the cut, `status` and the round count it apart from the counted votes. The browser's vote count includes its excluded votes, so the page shows no exclusion. Undo passes over excluded votes and never withdraws one, so only an exclusion inside the 10-minute undo window can show, as Undo turning unavailable or restoring the browser's earlier pair.

Speed, minority preferences and disagreement with the owner are signals to investigate. No vote is excluded automatically or for its outcome alone.

## Schema Changes

`web/migrations/` holds the arena's schema as Wrangler D1 migration files. `wrangler d1 migrations apply` records each applied file in `d1_migrations` and skips it afterward. The deploy job applies them to production and the operator to staging with the commands in [Running](../guide/running.md#setting-up-a-vote-store), while the Python and Worker tests apply the same files. Each schema change adds the next numbered file, such as `0002_<change>.sql`, and a merged migration file never changes. A migration must keep the running Worker working, since the deploy job applies it before deploying the code that needs it, and `wrangler rollback` restores code without reverting the schema. Migration files are the one exception to the rule against migrations, and they cover the schema alone: each holds the SQL its schema change needs, while no code moves, rewrites or re-imports votes.

## Vote API

| Route                                    | Request                           | Success                                                                                                 | Errors                                                     |
| ---------------------------------------- | --------------------------------- | ------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| `GET /api/families`                      | None                              | `{families: [{family, display_name, aspect}]}` for each active publication                              | None                                                       |
| `POST /api/actors`                       | `{token}`                         | 204 and the cookie, or 204 alone when a valid cookie already names a browser                            | 400 `challenge_failed`, 429 `daily_limit`                  |
| `GET /api/pair?family=<name>`            | Cookie                            | `{pair: {id, instruction, checklist, category, render, a, b, defects} \| null, votes, remaining, undo}` | 401 `admission_required` with `sitekey`, 404 `no_round`    |
| `POST /api/pair/skip`                    | `{pair}`                          | The same body as `GET /api/pair`                                                                        | 409 `stale_pair`                                           |
| `POST /api/votes`                        | `{pair, choice, defects: {a, b}}` | 204, also for a repeated identical request                                                              | 400 `invalid_request`, 409 `stale_pair`, 429 `daily_limit` |
| `POST /api/votes/undo`                   | `{family}`                        | 204, after which `GET /api/pair` returns the restored pair                                              | 409 `nothing_to_undo`                                      |
| `GET /api/freshness?family=<name>&<cut>` | None                              | `{status: "current" \| "stale" \| "unknown"}`                                                           | 400 `invalid_request`                                      |

- **Pair state.** `votes` counts the browser's votes in the family, `remaining` its candidates, and `undo` whether it has a counted vote in the family under 10 minutes old.
- **Recording.** One batch inserts the vote from the pending pair, copying its systems from the outputs and measuring `elapsed_ms` from the pair's issue, and then releases the pair. When nothing was inserted, the Worker answers 204 for a retry with the same choice and issues, `daily_limit` at the network's cap with the pair kept, and `stale_pair` otherwise. It accepts only the issues the publication lists.
- **Undo** restores the pair of the browser's latest counted vote under 10 minutes old, with its ID and issue time, in place of the pending one, and deletes that vote. Each call walks one vote further back.
- **Freshness.** A closed round's board sends, for each store it read, the cutoff as `production` or `local` and its counted votes as `production_votes` or `local_votes`. The Worker reads the store its `STORE` var names and answers `stale` when a counted vote follows the cutoff or the number of counted votes up to it has changed, so a later vote, an exclusion and a restore each mark the round stale. A round without this store's cutoff, as on staging, is `unknown`. Each data center reuses the answer for the same parameters for a minute, so a busy board reads the store once.
- **Errors.** Every response carries `Cache-Control: no-store`, and any route answers 429 `rate_limited` past the per-minute request limit. One error hook maps each refusal to its fixed code and anything else to 500 `unavailable`. Invocation logs are off, so Workers Logs hold only what that hook writes, the route and the error, and never an address, tag, cookie or body.

The sitekey comes from the Worker's `TURNSTILE_SITEKEY` var in the 401 body, so one site build serves every environment.

## Admission and Limits

`web/worker/limits.ts` holds every number below except the per-minute request limit, which the `ratelimits` tables of `web/wrangler.toml` set. Each control prices one lever an abuser has, and none depends on what a vote chose.

| Lever or need      | Control                                                                                                                                                                                          |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| A new browser      | One Turnstile solve on `POST /api/actors`, whose siteverify must succeed for the `TURNSTILE_HOSTNAME` var, then at most 5 new browsers per network tag in 24 hours                               |
| A browser's weight | The actor cap at close                                                                                                                                                                           |
| A network's votes  | At most 200 votes per network tag in 24 hours across families, where excluded votes still count and undone votes no longer do                                                                    |
| Request floods     | A Rate Limiting binding of 60 requests per 60 s, keyed by the browser, or by the network tag before admission                                                                                    |
| Forged identities  | The `__Host-bench-actor` cookie, HttpOnly, Secure, SameSite=Strict and signed with `ACTOR_KEY`, valid for 400 days. POST routes require `Origin` to equal `APP_ORIGIN`, under a 16 KB body limit |

Each cap is counted inside the insert it limits. A network tag is HMAC-SHA-256 under `IP_TAG_KEY` over the IPv4 address or the IPv6 /64. Raw addresses are never stored, in rows or in Workers Logs, and the daily cron clears tags older than 60 days from votes and browsers. The tag cap also binds the owner, so more than 200 votes in 24 hours from one home network answer `daily_limit` until the window moves on.
