# Site

The site under `web/` has two views built from one Astro project and one component set: the public results, built only from the committed reports, and the local vote page, which only `bench serve` hosts. [TypeScript](../conventions/typescript.md) gives the conventions for changing either.

## Results

Each family scored by votes gets a page at `/<family>/` showing one round, with the round and category filters kept in the URL:

- **Round summary:** the [decision](ranking.md#decision), naming the leader and the tie-break that chose it when another ranked system ties with the top score, or saying there is none while a preliminary system is in that tie. Beside it sit the vote and session counts, the votes left out of the fit, and three of the [diagnostics](ranking.md#diagnostics): rater agreement, separated pairs, and split-half τ. Rater agreement below the floor adds a warning.
- **Leaderboard:** a sortable table whose interval column is a forest plot on one axis shared by every row, with a rule at the reference system's score and hollow dots for preliminary systems. Choosing a category swaps in the systems that category's refit ranks, with no rank spread, and marks the columns that stay family-wide. The board's or the category's unranked systems follow the table by name, since they have no score.
- **Categories:** a system by category heatmap on a five-step sequential ramp, with each cell's score and vote count, and preliminary cells framed by a hatch. Its rows follow the family board's order whichever category is chosen, after which come the systems only a category ranks.
- **Cost and latency:** score with its interval against the mean cost per output and against the p50 latency, both on log axes, with the Pareto frontier drawn as a step line and its systems labelled. A chart with no priced or timed system is left out.

Each system has a page at `/<family>/<system>/` from its latest round: its score, rank spread, votes, and fingerprint, then head-to-head win rates, category scores, output tokens and latency beside the other systems, flag tallies, and its standing in every round it appeared in. Families scored by tests get a table of pass rates, and the home page lists every family with its leader.

Every chart has a table twin with the same values and a tooltip on hover, and both themes read their colors from CSS variables, so a theme switch needs no re-render.

## The Vote Page

`/vote/` exists only in builds with `BENCH_LOCAL=1`, which `bench serve` makes, and it fetches pairs from the API that [Arena](arena.md#vote-api) defines at runtime. It shows the brief above two outputs that scroll independently and render as written, with a toggle that renders text outputs as Markdown, while HTML outputs load in sandboxed frames from the render origin with viewport presets of 375, 768, and 1280 px. Nothing is preselected, and the verdicts, flags, note, submit, and undo each have a key, which [Voting](../guide/voting.md#casting-a-vote) lists. Each new pair takes focus back from the control that loaded it, so the next key is a shortcut again.

## Design Direction

The tokens and fonts match hakula.xyz: Inter Variable, with Sarasa Gothic SC or Source Han Sans SC for Chinese where installed, and Maple Mono for code. The leaderboard spends the one bold move, with the table and its forest plot reading as one object, while everything else stays quiet, with no card grid or hero number and numbers in tabular figures. Charts use one series hue and one emphasis hue, which the hovered or current system takes.

## Deploy

The public build deploys to Cloudflare Workers static assets at `bench.hakula.xyz`, which `web/wrangler.toml` configures. Each PR from this repository, except Dependabot's, which receive no secrets, gets a Worker Preview named `pr-<number>` under the same Worker. Its `workers.dev` URL serves the PR's latest build with `X-Robots-Tag: noindex`, and each push also gets a URL of its own. The site has no bindings or variables, so the `previews` block stays empty and production and previews differ only in hostname. [Development](../conventions/development.md#ci) lists the workflows.
