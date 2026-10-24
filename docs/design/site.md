# Site

The site under `web/` has two views built from one Astro project and one component set: the public results, built from committed families, systems, reports, and the nixos-config catalog, and the vote page, served with the voting runtime. [TypeScript](../conventions/typescript.md) gives their conventions, and [CI](../conventions/ci.md#deploy) covers deployment.

## Results

The site lists only families with `public = true` and their reports. Before its first round closes, a family shows unmeasured systems using current catalog names. Closed rounds use their own frozen public identities. Titles and meta tags use full family names. The build rejects incomplete report metadata and public family IDs without their aspect prefix.

A board lives at `/<aspect>/<slug>/`, where the slug is the family ID after its aspect prefix, so only a family rename moves it. The home page groups boards under their aspects at `/#<aspect>`. Family pages link sibling boards, and a system page links its family and the same system's pages on sibling boards.

Each board uses the shared scorer and render registries for its steps, labels, and methodology link. Step captions explain the family's exact configuration. A coding board uses Run → Tests → Blind pairs → Scores and the tests-then-votes methodology. Legends show only the marks a figure draws. Badges, column headers, and summary figures expose their definitions by tap, click, or Enter. A fallback anchor's definition names the reference it stands in for.

A votes board shows one round, with its round and category filters kept in the URL:

- **Summary:** vote and session counts, excluded votes, repeat consistency and split-half diagnostics. The uncertainty note states that inference for new cases and voters remains unavailable until calibrated. Round settings show the recorded method and rules.
- **Leaderboard:** a sortable table with point scores on one shared axis. Preliminary systems use hollow dots, derived from that round's decisive counts and threshold. The axis begins at most 600 points below the top score and represents clipped points with arrowheads. A category selection uses its refit and labels family-wide columns. Unranked systems follow by name.
- **Categories:** point plots share an axis starting at the lowest of their own cuts. Rows follow the family board's order, followed by systems ranked only in that category.
- **Cost and latency:** point scores against mean cost per output and p50 latency, with a labelled Pareto frontier. Each metric uses a log axis when every value is positive and a linear axis when it includes zero. A chart without priced or timed systems is omitted.

Each system's page uses its latest appearance for the heading, point score, rank, votes, head-to-head results, categories, output distributions and tagged issues. Each history row retains its own label, fingerprint, reference, anchor, method and rules. Compare scores within one family's round because the anchor can change. Tests-scored families show descriptive pass-rate tables with their report's frozen labels.

Every chart has a table twin with the same values, and its tooltips use values the twin also shows. Static point plots are tables themselves. Token and latency ranges show observed output percentiles. Charts read colors from CSS variables so theme changes need no re-render.

## The Vote Page

`/vote/` is emitted in every build. The page fetches pairs from the same-origin [vote API](arena.md#vote-api) at runtime, so no vote data enters a build. Real voting uses the production D1 ledger, and staging has a separate D1 ledger for validation. Admission runs in its own document at `/admit`. Static previews without the API show an unavailable state.

The prompt and the checklist are separate because the model never saw the checklist. Outputs scroll independently and render as text, diffs, or sandboxed HTML from the render origin. Only text outputs use the side-by-side prompt layout on wide screens, since a prompt column would squeeze HTML viewports and diffs. [Voting](../guide/voting.md) owns the controls, shortcuts, and rendering options.

Nothing is preselected. Each new pair clears the previous control's focus so the next key acts as a shortcut. The page reveals no system identity, timing, or server error details beyond the API's message.

## Design Direction

The site is named HAKULA†BENCH, after the owner's blog HAKULA†CHANNEL, with the dagger as part of the name. The wordmark and favicon retain decorative capped-line artwork. Actual ranking plots show point estimates and state the unavailable uncertainty contract.

The remaining design uses ink text, one ultramarine accent, tabular numbers, and no card grid or hero number. IBM Plex Sans sets text, Sarasa Gothic SC or Source Han Sans SC supplies Chinese where installed, and Maple Mono sets code. Charts use one series hue and one emphasis hue for the hovered or current system.
