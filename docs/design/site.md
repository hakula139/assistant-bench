# Site

The site under `web/` has two views built from one Astro project and one component set: the public results, built from committed families, systems, and reports, and the vote page, served with the voting runtime. [TypeScript](../conventions/typescript.md) gives their conventions, and [CI](../conventions/ci.md#deploy) covers deployment.

## Results

The site lists only families with `public = true` and their reports. Before its first round closes, a family shows unmeasured systems under their current display names. Closed rounds use their own frozen public identities. Titles and meta tags use full family names. The build rejects incomplete report metadata, public family IDs without their aspect prefix, and reports scored by another scorer than their family's.

A board's path shows its latest round, while each closed round keeps a permanent page. Round links preserve filters. Family pages link sibling boards, and system pages link appearances across those boards.

Each board uses the shared scorer and render registries for steps, labels, and its methodology link. Captions explain the family's configuration, and legends show only marks the figure draws. Definitions open by tap, click, or Enter. A fallback anchor's definition names the reference it stands in for.

A votes board shows one round. Category selections refit its scores without intervals, mark them as preliminary, and distinguish family-wide measurements. The board identifies systems its leader test cannot separate. Chart axes share the comparison's scale and identify clipped points. Cost and latency charts label their Pareto frontier and omit unavailable measurements.

Each system's page uses its latest appearance for its heading and results. Each history row retains its own label, fingerprint, reference, anchor, method and rules. Compare scores within one family's round because the anchor can change.

A tests board shows pass rates with their 95% intervals on one axis from 0 to 100% and the [reported measurements](reports.md#results) in its table. A category selection, kept in the URL, shows that category's pass rates and task counts. A note names the leader and the systems its paired test cannot separate from it.

Every chart has a table twin with the same values, and its tooltips use values the twin also shows. Static point plots are tables themselves. Token and latency ranges show observed output percentiles.

## The Vote Page

`/vote/` is emitted in every build. The page fetches pairs from the same-origin [vote API](arena.md#vote-api) at runtime, so no vote data enters a build. On public deployments, a browser the Worker has not admitted solves an interaction-only Turnstile challenge in place, whose frame the `/vote/*` rule in `web/public/_headers` allows. Turnstile is the page's only third-party content. A private deployment verifies Cloudflare Access before serving any page, asset or API and admits its owner when serving `/vote/`. The owner's allowed login addresses share one persistent actor. Static previews without the API show an unavailable state.

Each tab keeps its own draft only while the server still holds the same pair. Reloading never sends a vote, and closing the tab clears unsent choices on a shared device.

The prompt and the checklist are separate because the model never saw the checklist. Outputs render as text, diffs, or isolated HTML pages. HTML pages use a fixed responsive iframe viewport, and their internal scroll offsets cannot be restored by the parent page. [Voting](../guide/voting.md) owns the controls, shortcuts, and rendering options.

Nothing is preselected. Each new pair clears the previous control's focus so the next key acts as a shortcut. The page reveals no system identity, timing, or server error details beyond the API's message.

## Design Direction

The wordmark and favicon use decorative capped-line artwork. Votes ranking plots draw each score interval as a thin bar behind its point.

The remaining design uses ink text, one ultramarine accent, tabular numbers, and no card grid or hero number. IBM Plex Sans sets text, Sarasa Gothic SC or Source Han Sans SC supplies Chinese where installed, and Maple Mono sets code. Charts use one series hue and one emphasis hue for the hovered or current system.

Lists of aspects and families must scale to many more entries, so none uses a fixed slot per entry. Visible copy stays minimal, with details in tooltips or popovers that a tap also opens.

Prevent paragraphs, headings, labels and short notes from ending on a lone word. Check screenshots in both themes and at phone and desktop widths, since automated layout metrics miss these orphans.
