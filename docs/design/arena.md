# Arena

The arena shows blind pairs of outputs from a family scored by votes and records each choice. The [module map](architecture.md#module-map) identifies its implementation boundaries.

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
- **Settlements** come from the publication's verdicts and content digests by the rule in [Sessions and Pairs](#sessions-and-pairs).
- **Measurements.** Latency, tokens and cost come from the collected `outputs/<family>.jsonl`, matched by output key. An output missing from the file contributes null.
- **The report** names its evidence by each store's cutoff, null for a store closing did not read, without any digest of private content. It anchors on the reference the publication froze.

## Blinding

The browser receives the approved instruction and checklist, two output panes and opaque capabilities. The owner keeps the mapping to systems, fingerprints and source trials. Pane responses omit system names, timing, token counts, errors and request metadata. Output text can still identify its author, so public content review must check the exact rendered bytes.

## Sessions and Pairs

A browser session belongs to one family. The server retains its current lease and accepted choices, so tabs and retries share one authoritative state. A submitted command keeps its identity while its outcome is unresolved. Changing families preserves that pending command for its original session.

Only different outputs that passed their gates can form a human comparison. The scheduler balances initial pair coverage, then uses stored sampling points to favor close pairs with little evidence. Swapped repeats stay out of the fit. The offline actor cap also limits cumulative browser influence across the complete family evaluation.

Gate failures and identical outputs settle comparisons without a browser vote. A failing output loses, two failing outputs count as both bad, and identical outputs tie. Every pairing of two systems' samples on a case carries weight 1 / (the product of their sample counts), so one case contributes at most one settled vote per pair of systems. Settled comparisons have no display side.

A publication freezes these output facts, and only a later publication changes them. An undone or excluded vote stays out of the fit.

## Vote Store

Production D1 is the sole source of real votes, including votes cast from a MacBook. Staging uses a separate preview D1 for deployed validation.

`web/wrangler.toml` names each target's database, whose tables `web/migrations/0001_init.sql` defines. Until the voting Worker launches, the older Worker keeps serving its starter round from its own ledger databases, which `wrangler.toml` at the repository root configures. No command reads the votes cast there, and they are dropped at the launch.

## Moderation

Each vote row keeps its browser's actor ID, a keyed tag of its network and `elapsed_ms`, the time the voting Worker measured from issuing the pair to recording the vote. A tag is a digest of the network address under a key only the Worker holds, so `bench arena votes` lists tags as opaque handles and the owner selects one by copying it. The listing reads a family's votes in keyset pages and aggregates them in the `bench` process, which keeps the medians out of SQL.

`exclude` and `restore` are each one `UPDATE` of the `exclusion` column, selected by family and by one tag, one actor or a list of vote IDs bound as a single JSON parameter. A null `exclusion` means counted. Excluding skips votes already excluded, so each keeps its first reason, and the reported count is the votes that changed. The update covers only the votes recorded when it runs and blocks nothing, so later votes from the same tag or actor count until excluded too. An excluded vote stays in the store and keeps its comparison in `votes_comparison`, so its browser cannot vote that comparison again, while the cut, `status` and the round count it apart from the counted votes.

Speed, minority preferences and disagreement with the owner are signals to investigate. No vote is excluded automatically or for its outcome alone.

## Schema Changes

`web/migrations/` holds the arena's schema as Wrangler D1 migration files. `wrangler d1 migrations apply` records each applied file in `d1_migrations` and skips it afterward. The operator applies them to staging and production with the commands in [Running](../guide/running.md#setting-up-a-vote-store), while the Python tests apply the same files to an in-memory store. The voting Worker has not launched yet, so `0001_init.sql` is the whole schema: a schema change edits it, and the operator recreates the staging and production databases, dropping their votes.

## Vote API

Anonymous browsers qualify through abuse checks and receive bounded session authority. The native owner validates the configured origin, capability scope, command identity and current ledger state before accepting a choice. Browser and network admission quotas limit submissions, while the offline cap limits one browser actor's total ranking influence. Private management has no public HTTP route.
