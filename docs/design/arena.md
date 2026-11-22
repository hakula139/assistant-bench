# Arena

The arena shows blind pairs of outputs from a family scored by votes and records each choice. The [module map](architecture.md#module-map) identifies the `bench` side's implementation boundaries, and `web/worker/` holds the voting Worker that [TypeScript](../conventions/typescript.md#the-voting-worker) describes.

## Publication and Closing

The `bench` process publishes outputs to a vote store and reads votes back through Cloudflare's D1 REST API. [Running](../guide/running.md#voting) covers credentials and the publication procedure. `web/wrangler.toml` names the account and each target's database.

Production votes on the public and synthetic cases of a family that sets `public_voting`. Every other case is voted in the private cloud store. `bench close` combines both stores. `prepare` pins the family's sealed output collection and reconstructs its recorded family, cases and system names, keeps the listed systems' outputs for the selected target, and stages the normalized publication in private D1. A production preparation requires `public_voting`. `inspect` reconstructs the prepared publication and renders its instruction, checklist, passing outputs under system names, and failing or ungraded outputs with gates from the pinned collection. Reviewing this listing and publishing its exact ID approves it.

- **Identities.** The publication ID digests the publication's content, including its target, without the ID. Every prepared read reconstructs the normalized rows and verifies this digest. Staging accepts production publications, and a private publication can only enter the private store. A case's key digests its instruction and checklist, and an output's key digests its family, job and trial, so republishing unchanged outputs keeps their keys and the votes on them. Duplicate cases are refused before partitioning them between stores.
- **Task verdicts.** A task output's tests set its verdict even after its agent errored, so an errored solution whose tests passed can join voted pairs, one whose tests failed loses its settled comparisons, and one whose tests never ran is ungraded and sits out of every comparison. The system's pass rate counts them by the [test pass rate](ranking.md#test-pass-rates) rule.
- **Content.** Only passing outputs carry content, since only they can form a voted pair. Every output carries its verdict, length and content digest, which settle the comparisons no one votes on. HTML content is the unchanged eligible document extracted during collection, its digest covers that document, and its text length is null.
- **Publishing** refuses a private case in a production publication. Preparation inserts cases and outputs idempotently, verifies their complete content identity, and records immutable collection and case provenance. Publishing stages the reviewed projection in the selected store and derives each system pair's eligible cases before activation. It activates the publication only once every row and eligible comparison is staged, so an interrupted run completes on a rerun and an already active ID changes nothing.

Preparing an existing publication ID compares every published output's complete canonical record in the new and original collections. Identical records reuse the original preparation and provenance, which the command prints. Different measurements or gates require an explicit regrade or rerun before publishing. Outputs outside that publication do not affect this comparison.

`bench close` takes cuts at the same timestamp from production and private D1. Each cut reads inventory, ballots and exclusions in one SQL statement. The stores have independent read views. When a family no longer allows public voting, its past production votes still count and any remaining active production inventory is ignored. A store with neither active inventory nor eligible ballot history contributes no cut.

Closing verifies the counts and canonical digest of both cuts before sealing them as content-addressed facts in private D1. A snapshot freezes the category order, closing code revision and each active publication's collection pin. `--snapshot <id>` recloses a sealed snapshot from its frozen facts.

- **Ballots.** A vote counts once its [undo window](#vote-api) expires if both original outputs passed on its case and have different content. Each vote follows its publication ID to the original case, category, split and output metadata. An edit to the instruction or checklist creates a new case key and leaves the old ballots attached to the original question. Excluded and ineligible votes count in `dropped_votes`.
- **Pooling.** Active cuts must agree on the reference, defects and shared system labels, and their case and output keys must be disjoint. System fingerprints agree across active and historical evidence. Current labels take precedence for published systems, and retired systems retain a label from their ballots. Browser identities are scoped to their store. Eligible copies of the same submission in both stores count once. Conflicting copies stop closing. Moderation still applies to each store separately.
- **Settlements and measurements.** Active inventory supplies settled comparisons and pins any output collection used for latency, tokens and cost. A missing collection pin or output contributes null. Historical ballots contribute votes and their own tags and lengths.
- **Report.** Each store's cutoff is the largest vote ID older than the undo window, or zero. The report records the count of votes included in each cut. Candidate contents and network tags stay outside the sealed facts and public report.

### Delayed standings

The leaderboard command captures one aggregate per public votes family per UTC hour. It compares each capture with the previous candidate and skips identical standings. A changed candidate stays in private D1 for two hours after capture. The deploy exports the latest eligible aggregate for each family and confirms it after the site deploy succeeds. Captures with no eligible comparisons clear the current standings once the delay expires. Dated reports in `reports/` remain available at their existing dates.

## Blinding

The browser receives the approved instruction and checklist, the case's category, the two outputs' text or diff and a random pair ID. HTML pairs omit output strings and derive two authenticated preview URLs from the pair ID and side. System names, fingerprints, output keys, timings and per-system counts never leave the Worker, and its error responses carry only fixed messages. Output content can still identify its author, so reviewing a publication's listing must check the exact content the page shows.

## Pairs and Skips

Balanced coverage chooses what a voter compares, and a keyed per-voter order breaks every tie, so no skip, reload, undo or retry can draw again.

- **Candidates.** For a browser and family, a candidate is a case of the active publication and an unordered system pair where both systems have a passing output on the case, some pair of those outputs differs in content, and the browser has no vote on that comparison.
- **Keyed rank.** Each rank is HMAC-SHA-256 under `ACTOR_KEY` over the browser, the family and the item, which gives each voter a fixed order the voter can neither predict nor change.
- **Draw.** The candidate system pair with the fewest counted votes in the family wins, ties going to the lowest rank. Within it, the case with the lowest rank wins, then the distinct-content sample pair with the lowest rank, whose lowest bit orders the panes.
- **Pending pair.** Each browser has one per family. `GET /api/pair` returns it while its publication is active, so a reload or a second tab sees the same pair. It goes when voted, skipped or replaced by a new publication, and the daily cron deletes one 30 days after its issue.
- **Skip** keeps the system pair and moves to the next case in the voter's order for it, wrapping around, under a new pair ID, so a late vote on the skipped ID is stale. With no other case, the pair stays. A skip records nothing else, and coverage counts only votes.

The system pair is fixed before the voter sees anything, and only a vote releases it, so skipping never reaches a pair holding a recognized system. Undoing and voting again leaves every count where it was, so the next draw repeats.

Gate failures and identical outputs settle comparisons without a browser vote. A failing output loses, two failing outputs count as both bad, and identical outputs tie. Every pairing of two systems' samples on a case carries weight 1 / (the product of their sample counts), so one case contributes at most one settled vote per pair of systems. Settled comparisons have no display side.

A publication freezes these output facts, and only a later publication changes them. An undone or excluded vote stays out of the fit.

## Vote Store

Production D1 holds public votes. Staging has a separate D1 database for validating publications and runtime changes. Private D1 holds the cases that production cannot publish, prepared publications and sealed vote snapshots.

`web/wrangler.toml` names each target's database, whose tables `web/migrations/` defines, and the voting Worker in `web/worker/` serves each target's vote API from it.

## Private Voting

The private Worker serves the same vote API behind its configured access policy, using the [private publication procedure](../guide/running.md#private-voting). Its D1 database and signing keys persist independently of the execution checkout. Cases retain their actual source and the coarse split `public`, `held-out` or `private` in the store.

## Moderation

Each vote row keeps its browser's actor ID, a keyed tag of its network and `elapsed_ms`, the time the voting Worker measured from issuing the pair to recording the vote. A tag is a digest of the network address under a key only the Worker holds, so `bench arena votes` lists tags as opaque handles and the owner selects one by copying it.

`exclude` and `restore` each atomically update existing votes selected by family and one tag, one actor or a list of vote IDs. Excluding skips votes already excluded, preserving their first reason, and reports how many changed. The update covers only votes recorded when it runs, so later votes from the same tag or actor count until excluded too.

An excluded vote stays in the store and keeps its comparison, so its browser cannot vote that comparison again. The cut, `status` and round distinguish excluded votes from counted votes, while the browser's vote count includes both and shows no exclusion. An exclusion inside the [undo window](#vote-api) can change which vote is available to undo. A closed report keeps its votes until the next `bench close`, which counts the excluded ones in `dropped_votes`.

Speed, minority preferences and disagreement with the owner are signals to investigate, and none alone is a reason to exclude. No vote is excluded automatically or for its outcome alone.

## Schema Changes

`web/migrations/` holds the arena's schema as Wrangler D1 migration files. `wrangler d1 migrations apply` records each applied file in `d1_migrations` and skips it afterward. The deploy job applies them to production and the operator to staging with the commands in [Running](../guide/running.md#setting-up-a-vote-store), while the Python and Worker tests apply the same files. Each schema change adds the next numbered file, and a merged migration file never changes. A migration must keep the running Worker working, since the deploy job applies it before deploying the code that needs it, and `wrangler rollback` restores code without reverting the schema. Migration files are the one exception to the rule against migrations, and they cover the schema alone: each holds the SQL its schema change needs.

## Vote API

The shared request and response schemas define the routes. These contracts constrain clients and storage:

- **Pair state.** `votes` counts the browser's votes in the family and `remaining` its candidates. Pair and skip responses carry the latest undoable vote's opaque pair ID in the `Arena-Undo` header, or `none` when there is no target. The JSON `undo` flag mirrors that availability so open tabs can still read the response across a deploy.
- **HTML previews.** HTML pair state omits `a` and `b`. Each preview reads one selected output only while the actor owns that pending pair in the active HTML publication. Voting, skipping or changing publication invalidates the URL. Undo gives restored previews new URLs. Responses use private no-store caching, no referrer, disabled DNS prefetch and a sandbox CSP allowing only inline CSS and data images or fonts. The empty-sandbox iframe cannot execute JavaScript, submit forms, load external dependencies or access its parent's origin. Native disclosures and current-document fragments remain available. Chromium can omit the Strict cookie when reloading the iframe itself, so an unavailable preview instructs the voter to reload the outer voting page.
- **Recording.** The Worker copies the pending pair's systems from the outputs and measures `elapsed_ms` from the pair's issue. Recording releases any pending receipt for the same browser and comparison in the same transaction. A retry with the same choice and issues answers 204. A receipt without a pending pair or matching vote answers `stale_pair`. The Worker accepts only the issues the publication lists.
- **Batch voting.** An admitted browser can send a family and a count from 1 to 8 to `POST /api/pair/batch`. The Worker returns pending blinded pairs, including any current pair. A browser finishes its open batch before receiving more batch pairs. Changing the active publication clears pending receipts for that family. Submit the family and up to 8 ordinary vote requests to `POST /api/votes/batch`. Each receipt returns `recorded`, `identical`, `stale_pair`, or `invalid_request` in request order. The accepted votes share a server timestamp and one transaction. Review each pair before submitting. Undo withdraws the latest eligible vote and reissues its pair under a fresh ID.
- **Confirmation.** `POST /api/votes/confirm` reads the browser's vote matching the pair ID in the original submission. It answers 204 when the choice and issue sets match and 409 `stale_pair` otherwise. The browser checks once after a network or gateway failure on its initial submission. If there is no match, it refreshes the pair before offering a manual retry.
- **Undo.** The request names a family and a vote's pair ID. The Worker withdraws it if it is still the browser's latest counted vote in the family and was recorded less than 10 minutes ago. The transaction restores the exact comparison with its original issue time and a fresh pair ID, replacing the ordinary pending pair. Remaining batch receipts stay open. A repeat or stale target returns `nothing_to_undo` so the client can refresh before attempting another Undo.
- **Errors.** Every response carries `Cache-Control: no-store`, and any route answers 429 `rate_limited` past the per-minute request limit. One error hook maps each refusal to its fixed code and anything else to 500 `unavailable`. Invocation logs are off, so Workers Logs hold only what that hook writes, the route and the error, and never an address, tag, cookie or body.

On public deployments, the sitekey comes from the Worker's `TURNSTILE_SITEKEY` var in the 401 body, so one site build serves every environment. The private Worker verifies an Access JWT's signature, issuer, audience, expiry, subject and allowed owner email before serving any response. Its `/vote/` HTML response creates the configured owner actor if absent and issues the same signed cookie. A private API request without that owner's valid cookie answers 401 `reload_required`, which asks the browser to reload the page without showing Turnstile. The owner's actor does not expire with its original creation time, and its configured opaque ID preserves progress across allowed login addresses and devices.

## Admission and Limits

`web/worker/limits.ts` holds every number below except the per-minute request limit, which the `ratelimits` tables of `web/wrangler.toml` set. Each control prices one lever an abuser has, and none depends on what a vote chose.

| Lever or need     | Control                                                                                                                                                                                          |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| A new browser     | One Turnstile solve on `POST /api/actors`, whose siteverify must succeed for the `TURNSTILE_HOSTNAME` var, then at most 5 new browsers per network tag in 24 hours                               |
| Request floods    | A Rate Limiting binding of 60 requests per 60 s, keyed by the browser, or by the network tag before admission                                                                                    |
| Forged identities | The `__Host-bench-actor` cookie, HttpOnly, Secure, SameSite=Strict and signed with `ACTOR_KEY`, valid for 400 days. POST routes require `Origin` to equal `APP_ORIGIN`, under a 16 KB body limit |

The admission insert counts new browsers on the same network tag and returns `daily_limit` once the quota is full. Admitted browsers can vote on every eligible comparison. A network tag is HMAC-SHA-256 under `IP_TAG_KEY` over the IPv4 address or the IPv6 /64. Raw addresses are never stored, in rows or in Workers Logs, and the daily cron clears tags older than 60 days from votes and browsers.
