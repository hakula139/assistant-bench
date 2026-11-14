# TypeScript

Conventions for the site under `web/` that the tooling doesn't already state.

## Layout

```text
web/src/
├── components/boards/       # The hydrated boards, plus the tables their pages share
├── components/charts/       # Observable Plot charts and their table twins
├── components/scorers/      # The scorer registry and each scorer's board, home panel, and methodology section
├── components/site/         # Shared page composition, navigation, terminology, and static plots
├── components/ui/           # shadcn/ui copies
├── components/vote/         # The vote island
├── content.config.ts        # The families, reports, and public systems collections
├── env.d.ts                 # The shape of .astro modules for plain TypeScript and ESLint
├── integrations/voting.ts   # Injects the vote route
├── layouts/                 # The page shell
├── lib/                     # Schemas, domain helpers, presentation rules, and vote API client
├── pages/                   # Public routes only
├── styles/global.css        # Tokens, fonts, and the shadcn/ui and chart variables
├── test/                    # The fixture, JSON Schema, chart stub, score track, and vote pair helpers, and the page, built-site, and Wrangler config tests
└── vote/vote.astro          # The vote page, routed by the integration
```

## Comments

Use `/** */` TSDoc for comments on declarations and members so editors show them on hover. Use `//` inside function bodies.

## Reports

The site reads committed public families, system labels and reports. Nothing from the private data directory may enter a build. Keep the strict Pydantic and Zod report schemas in step, including nullability and public identity.

Browser validation uses `zod/mini`. Classic Zod probes `eval`, which violates the page CSP, so classic schema construction stays in the content loaders and their schema modules. Other consumers import their types.

Dispatch scorer and render behavior through the shared registries, including methodology links. Family names must not select presentation behavior.

## Components

- Add shadcn/ui components with `pnpm exec shadcn add <component>` from `web/`, then adjust the committed copy. Preserve keyboard behavior, available-space limits and control contrast.
- Prefer component variants and Tailwind utilities, with raw CSS reserved for theme tokens. Shared appearances have one component, variant or class constant.
- Keep running text within `measure` and page blocks `stack` apart. Use flex `gap` between stacked blocks because Astro's `display: contents` island wrappers break sibling-margin spacing.
- Hydrate only interactive components and keep island props serializable. Filters use `useSearchParam`. Choices carrying different datasets, such as closed rounds, use static routes so each page carries only its own data.

## Charts

Use `PlotFigure` and the shared chart primitives for responsive sizing, measured label margins, marks and axes. Derive point-plot domains consistently and show clipped outliers. Closed rounds use their frozen method, rules and public identities.

Use `logTicks` with the axis mark's `text` option because Plot's default log formatter blanks labels on spans under a decade. Take colors from CSS variables. Recheck lightness, chroma, color-vision separation and contrast in both themes before changing the series and emphasis hues.

Every chart needs a table twin with the same values, and tooltips may show only values available there. Static point plots can serve as their own table. [Site](../design/site.md) owns presentation and interaction requirements.

## The Voting Worker

The client and routes share the API schemas in `lib/arena.ts`. Use `zod/mini` as a namespace import, since its named `z` export retains the full Zod bundle. Keep bindings generated from `web/wrangler.toml` out of version control.

A write that must not interleave with another is one SQL statement or one D1 batch, which D1 executes transactionally. [Arena](../design/arena.md) owns admission, fixed error responses, blinding and schema evolution. [Site](../design/site.md#the-vote-page) owns the voting interaction.
