# Arena

The arena shows blind pairs of outputs from a family scored by votes and records each choice. The [module map](architecture.md#module-map) identifies its implementation boundaries.

## Publication and Evidence

`bench arena` manages immutable releases through a named native Worker profile in `arena/wrangler.toml`. The private Service binding admits typed publication, export and moderation operations. A target must be explicitly configured before its commands can connect.

```bash
uv run bench arena prepare selection.json release.json
uv run bench arena publish production release.json
uv run bench arena status production writing-zh
uv run bench arena health production
uv run bench arena export production writing-zh evidence.json
uv run bench arena refresh production evidence.json
uv run bench close writing-zh --snapshot evidence.json
```

The selection names a rendered case family and exact `outputs`, each identified by `case`, `system`, `job` and `trial`. Preparation re-collects current trials, checks the resolved family fingerprints, and proves the rendered tasks against their native Harbor locks. Every selected sample of a case must have received the same instruction and source inputs. The prepared artifact contains the frozen numerical evaluation, presentation, rendered outputs and initial sampling points.

Preparation requires the family's `public_voting` opt-in and an exact `PublicApproval`: source and license attestations for the selected cases, plus content and presentation digests for every published case and output. Synthetic classification and passing gates alone do not authorize publication. Regeneration changes the approved output identities and requires another review.

Publication seals the replacement before activating it with the current family revision. A presentation refresh retains the active fitted points when its numerical evaluation is unchanged. `refresh` fits the saved evidence offline, then updates sampling points while retaining the active presentation. Concurrent evidence or control changes can refuse activation, so inspect the current status before choosing another cut.

`status` includes the deployment's aggregate network-association expiry lag in milliseconds. `health` checks the selected deployment and exits unsuccessfully while expired associations await cleanup.

An export captures every section at registered immutable cutoffs. Its authoritative section counts detect missing pages, while its content digest detects later file changes. Captures contain numerical evidence and provenance without network associations. `bench close --snapshot` reads the saved inventory, labels and measurements without consulting the running owner or current catalog. The raw accepted count includes repeats and withdrawn or moderated receipts. The actor cap applies once to all eligible ordinary votes before any report subset or diagnostic is fitted.

Generate the application's safe static family list from approved releases:

```bash
uv run bench arena catalog web/public/arena-families.json release.json
```

The catalog contains only family IDs, display labels, aspects and available defect tags. It carries no case, output or system inventory.

For private administration, `bench arena investigate <target> filters.json` returns one bounded receipt page with its continuation sequence. Filters can select a family, actor, session, evaluation, time range, current status or an unexpired keyed network tag. Continue with `--after <sequence>`. `bench arena moderate <target> plan.json` stages and seals an exact reviewed exclusion or restoration plan, then applies its bounded chunks. Repeating the same plan resumes its immutable commands. Network associations remain searchable for 60 days and are excluded from permanent numerical exports.

## Blinding

The browser receives the approved instruction and checklist, two output panes and opaque capabilities. The owner keeps the mapping to systems, fingerprints and source trials. Pane responses omit system names, timing, token counts, errors and request metadata. Output text can still identify its author, so public content review must check the exact rendered bytes.

## Sessions and Pairs

A browser session belongs to one family. The server retains its current lease and accepted choices, so tabs and retries share one authoritative state. A submitted command keeps its identity while its outcome is unresolved. Changing families preserves that pending command for its original session.

Only different outputs that passed their gates can form a human comparison. The scheduler balances initial pair coverage, then uses stored sampling points to favor close pairs with little evidence. Swapped repeats measure consistency and carry zero fitting mass. The offline actor cap also limits cumulative browser influence across the complete family evaluation.

Gate failures and identical outputs settle comparisons without a browser vote. A failing output loses, two failing outputs count as both bad, and identical outputs tie. Every pairing of two systems' samples on a case carries weight 1 / (the product of their sample counts), so one case contributes at most one settled vote per pair of systems. Settled comparisons have no display side.

The numerical evaluation freezes these output facts. A later publication changes them through a new evaluation, while a presentation-only refresh keeps the same numerical identity. Withdrawals and moderation append evidence events. An export replays the captured history and keeps inactive receipts in its accepted denominator.

## Vote Store

Production D1 is the sole source of real votes, including votes cast from a MacBook. Staging uses a separate preview D1 for deployed validation.

The ledger holds immutable evaluations, approved publications, sampling snapshots, receipt history and guarded control events. Typed private management commands publish and investigate these records. Database bindings belong to the native owner, while the management client has only its authenticated Service capability.

## Vote API

Anonymous browsers qualify through abuse checks and receive bounded session authority. The native owner validates the configured origin, capability scope, command identity and current ledger state before accepting a choice. Browser and network admission quotas limit submissions, while the offline cap limits one browser actor's total ranking influence. Private management has no public HTTP route.

## Render Origin

HTML outputs load from a separate cookie host inside sandboxed frames. The renderer receives a narrow read capability for the current authorized output and has no database or management binding. Its content policy blocks network requests and permits only the output's contained scripts, styles and data images. Text and diff panes retain the same identity blinding.
