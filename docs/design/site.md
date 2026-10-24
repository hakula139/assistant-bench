# Site

The site under `web/` has two views built from one Astro project and one component set: the public results, built from committed families, systems, reports, and the nixos-config catalog, and the local vote page, which only `bench serve` hosts. [TypeScript](../conventions/typescript.md) gives their conventions, and [CI](../conventions/ci.md#deploy) covers deployment.

## Results

The site lists only families with `public = true` and their reports. Before its first round closes, a family shows its systems with unbounded intervals and its reference at 1,000. Titles and meta tags use full family names, while system labels use display names resolved from `systems.toml` and the catalog, as [Authoring](../guide/authoring.md) defines them. The build fails when a family or report names a system without a table, or a public family's ID does not start with its aspect.

A board lives at `/<aspect>/<slug>/`, where the slug is the family ID after its aspect prefix, so only a family rename moves it. The home page groups boards under their aspects at `/#<aspect>`. Family pages link sibling boards, and a system page links its family and the same system's pages on sibling boards.

Each board uses the shared scorer and render registries for its steps, labels, and methodology link. Step captions explain the family's exact configuration. A coding board uses Run → Tests → Blind pairs → Scores and the tests-then-votes methodology. Legends show only the marks a figure draws. Badges, column headers, and summary figures expose their definitions by tap, click, or Enter. A fallback anchor's definition names the reference it stands in for.

A votes board shows one round, with its round and category filters kept in the URL:

- **Summary:** the [decision](ranking.md#decision), vote and session counts, votes left out of the fit, and the [diagnostics](ranking.md#diagnostics). A tied leader's hint gives the tie-break, and low rater agreement adds a warning.
- **Leaderboard:** a sortable table with score intervals on one shared axis. Preliminary systems use hollow dots and dashed intervals. The axis begins at most 600 points below the top score, a gap worth about 97% of the votes, and marks values beyond that cut with arrowheads. Hidden chart or table columns remain available as score text or in score popovers, so the table fits without scrolling. A category selection uses that category's refit, omits rank spread, and labels family-wide columns. Unranked systems follow by name.
- **Categories:** forest plots share an axis starting at the lowest of their own cuts, so wider intervals remain visibly wider. Rows follow the family board's order, followed by systems ranked only in that category.
- **Cost and latency:** scores and intervals against mean cost per output and p50 latency, with a labelled Pareto frontier. Each metric uses a log axis when every value is positive and a linear axis when it includes zero. A chart without priced or timed systems is omitted.

Each system's page uses its latest round for its score, rank spread, votes, head-to-head results, category scores, token and latency comparisons, and tagged issues. Its round history links each date to the fingerprint measured then. Raw scores are comparable only within one family's round, since the anchor can change between rounds. Tests-scored families show pass-rate tables.

Every chart has a table twin with the same values, and its tooltips use values the twin also shows. Static forest plots are tables themselves, with each score opening its interval. Charts read colors from CSS variables so theme changes need no re-render.

## The Vote Page

`/vote/` exists only in builds with `BENCH_LOCAL=1`, which `bench serve` makes. The page fetches pairs from the [vote API](arena.md#vote-api) at runtime, so no vote data enters a build.

The prompt and the checklist are separate because the model never saw the checklist. Outputs scroll independently and render as text, diffs, or sandboxed HTML from the render origin. Only text outputs use the side-by-side prompt layout on wide screens, since a prompt column would squeeze HTML viewports and diffs. [Voting](../guide/voting.md) owns the controls, shortcuts, and rendering options.

Nothing is preselected. Each new pair clears the previous control's focus so the next key acts as a shortcut. The page reveals no system identity, timing, or server error details beyond the API's message.

## Design Direction

The site is named HAKULA†BENCH, after the owner's blog HAKULA†CHANNEL, with the dagger as part of the name. The 95% interval is its visual identity: the wordmark and favicon use a capped interval and dot, and the home page leads with each family's forest plot.

The remaining design uses ink text, one ultramarine accent, tabular numbers, and no card grid or hero number. IBM Plex Sans sets text, Sarasa Gothic SC or Source Han Sans SC supplies Chinese where installed, and Maple Mono sets code. Charts use one series hue and one emphasis hue for the hovered or current system.
