# Site

The site under `web/` has two views built from one Astro project and one component set: the public results, built only from the committed reports, and the local vote page, which only `bench serve` hosts. [TypeScript](../conventions/typescript.md) gives the conventions for changing either, and [CI](../conventions/ci.md#deploy) covers how the public build deploys.

## Results

The site lists the families whose `family.toml` sets `public = true`, which [Authoring](../guide/authoring.md#families) defines, together with their reports, so test families stay off it. A listed family appears before its first round closes, showing its systems with unbounded intervals and its reference at 1,000.

Each family scored by votes gets a page at `/<family>/` showing one round, with the round and category filters kept in the URL:

- **Round summary:** the [decision](ranking.md#decision), naming the leader and the tie-break that chose it when another ranked system ties with the top score, or saying there is none while a preliminary system is in that tie. Beside it sit the vote and session counts, the votes left out of the fit, and three of the [diagnostics](ranking.md#diagnostics): rater agreement, separated pairs, and split-half τ. Rater agreement below the floor adds a warning.
- **Leaderboard:** a sortable table whose interval column is a forest plot on one axis shared by every row, with a rule at the reference system's score and hollow dots on dashed intervals for preliminary systems. The axis starts at most 600 points below the top score, at which the top system would take about 97% of the votes, so an interval that runs past the start ends in an arrowhead and a score past it sits there as one, with the score printed beside it in charts that have no score column. Choosing a category swaps in the systems that category's refit ranks, with no rank spread, and marks the columns that stay family-wide. The board's or the category's unranked systems follow the table by name, since they have no score.
- **Categories:** one forest plot per category, all on one axis that covers each category's own cut below its own top score, so a category's wider intervals read as wider beside the others. Their rows follow the family board's order whichever category is chosen, after which come the systems only a category ranks.
- **Cost and latency:** score with its interval against the mean cost per output and against the p50 latency, both on log axes, with the Pareto frontier drawn as a step line and its systems labelled. A chart with no priced or timed system is left out.

Each system has a page at `/<family>/<system>/` from its latest round: its score, rank spread, votes, and fingerprint, then head-to-head win rates, category scores on the family page's category axis, output tokens and latency beside the other systems, flag tallies, and its standing in every round it appeared in. Families scored by tests get a table of pass rates, and the home page shows each listed family's latest scores as a compact forest plot with its leader, followed by its unranked systems and those without votes.

Every chart has a table twin with the same values and a tooltip on hover. The static forest plots on the home page, on a family page before its first round, and in the methodology are tables themselves, with each row's values in text and in a hover title. Both themes read their colors from CSS variables, so a theme switch needs no re-render.

## The Vote Page

`/vote/` exists only in builds with `BENCH_LOCAL=1`, which `bench serve` makes, and it fetches pairs from the API that [Arena](arena.md#vote-api) defines at runtime. It shows the brief above two outputs that scroll independently and render as written, with a toggle that renders text outputs as Markdown, while HTML outputs load in sandboxed frames from the render origin with viewport presets of 375, 768, and 1280 px. Nothing is preselected, and the verdicts, flags, note, submit, and undo each have a key, which [Voting](../guide/voting.md#casting-a-vote) lists. Each new pair takes focus back from the control that loaded it, so the next key is a shortcut again.

## Design Direction

The site is named HAKULA†BENCH, after the owner's blog HAKULA†CHANNEL, with the dagger as part of the name. The 95% interval is its identity: the mark beside the wordmark and in the favicon is a capped interval with its dot, the home page leads with each family's forest plot, and the empty state draws every interval unbounded. Everything else stays quiet, with ink text, one ultramarine accent, no card grid or hero number, and numbers in tabular figures. IBM Plex Sans sets all text, with Sarasa Gothic SC or Source Han Sans SC for Chinese where installed, and Maple Mono for code. Charts use one series hue and one emphasis hue, which the hovered or current system takes.
