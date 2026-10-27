# TypeScript

Conventions for the site under `web/` that the tooling doesn't already state.

## Layout

```text
web/src/
├── components/boards/                      # The hydrated boards, plus the tables their pages share
├── components/charts/                      # Observable Plot charts and their table twins
├── components/scorers/                     # The scorer registry and each scorer's board, home panel, and methodology section
├── components/site/                        # Shared page composition, navigation, terminology, and static plots
├── components/ui/                          # shadcn/ui copies
├── components/vote/                        # The vote island
├── content.config.ts                       # The families, reports, systems, and models collections
├── env.d.ts                                # The shape of .astro modules for plain TypeScript and ESLint
├── integrations/voting.ts                  # Injects the vote route
├── layouts/                                # The page shell
├── lib/                                    # Schemas, domain helpers, presentation rules, and vote API client
├── pages/                                  # Public routes only
├── styles/global.css                       # Tokens, fonts, and the shadcn/ui and chart variables
├── test/                                   # The fixture, JSON Schema, and chart stub helpers, and the page and built-site tests
└── vote/vote.astro                         # The vote page, routed by the integration
```

## Comments

- When a module-level declaration or its interface, type, or class member needs a comment, use `/** */` TSDoc so editors show it on hover. Use `//` for other comments, including those inside a function body.

## Checks

- `pnpm lint` enforces import order with perfectionist's `sort-imports` and `sort-named-imports`, which `eslint --fix` applies.
- `pnpm test` checks every built page with axe-core, as [Testing](testing.md#accessibility) describes.

## Reports

- The site reads only `families/*/family.toml` and `reports/*/*.json` through the `families` and `reports` collections' `glob()` loaders, `systems.toml` through the `systems` collection's `file()` loader, and the export that `BENCH_NIXOS_CONFIG` names through the `models` collection's inline loader, so `pnpm build` and `pnpm typecheck` run only inside the dev shell. It drops every family without `public = true` together with its reports. Nothing under the data directory reaches a build.
- `lib/family.ts` lists only the `family.toml` keys the site reads, with objects that drop the rest, since the pydantic settings model already rejects unknown keys.
- `lib/report.ts` mirrors the Pydantic `Report` model field for field with strict Zod objects. Change both schemas in one PR so the build rejects unknown fields and invalid values.
- Only `content.config.ts`, `lib/family.ts`, and `lib/report.ts` build classic Zod schemas, and other modules import only types from the last two, as ESLint enforces. Building a classic schema in the browser runs Zod's eval probe, which the pages' script policy reports as a violation. Browser code that validates data, such as the vote client's `lib/arena.ts` and `lib/vote-flow.ts`, uses `zod/mini`, which never probes.
- `pnpm test` parses both report fixtures and compares Zod's JSON Schema with the Pydantic-generated `tests/fixtures/report.schema.json`. It checks field types, nullability, constants, enums, required and allowed keys, and map keys within each results variant. Numeric bounds, formats and descriptions are not compared.
- Add a scorer's schema variant and register its views in `components/scorers/index.ts`, its metadata and steps in `lib/method.ts`, and its icons in `components/site/Method.tsx`. Pages dispatch through the registry, and methodology links must reach the registered section. Keep render-specific steps, labels, and sections in those shared registries, keyed by scorer and render, never by family name.
- Register a public aspect's title in `lib/aspects.ts` and its icon in `components/site/Method.tsx`. The registry determines listing order.
- A new defect ID updates the `Defect` type in `src/assistant_bench_arena/models.py`, the `defects` list in `lib/defects.ts`, and its label in `lib/glossary.ts`. A new language needs a detector in `scoring/gates.py`, the `Language` type in `families.py`, the enum in `lib/family.ts`, and its label in `lib/method.ts`.

## Components

- Add shadcn/ui components with `pnpm exec shadcn add <component>` from `web/`, then commit the copy and adjust it in place. Preserve keyboard behavior, available-space limits, and control contrast when changing a component.
- Style with the shadcn/ui components and their variants first, and with Tailwind utilities where none fits, keeping raw CSS to theme tokens in `global.css`. When several elements share a look, give it one home, such as a component variant, a component like `Section`, or a shared class constant like `note` in `components/site/PageParts.tsx`, so the class list is not repeated.
- Running text stays within `measure`, and page blocks sit `stack` apart, which `Base.astro` applies to every results page's top level. Space stacked blocks with flex `gap` and no margins on the blocks, as `stack`, `PageHeader`, `Section`, and `Part` do, since Astro wraps each hydrated island in a `display: contents` element that `space-y` margins skip.
- Hydrate a component only where the page is interactive. Tables render as static React components unless a cell opens a hint, and island props stay serializable, which is why `DotRangePlot` takes a named format.
- Pass filter state through `useSearchParam` so each filter lands in the URL. When each value of a choice would add its own data to the page, as a family's rounds do, give each value a static route and link the routes, so island props hold only what the page shows.

## Charts

- Render every chart through `PlotFigure`, which sizes it to its container and measures labels in the chart's font. Size label margins from those measurements, never from a fixed width.
- Pick log-axis ticks with `logTicks` and label them through the axis mark's `text` option, because Plot's log tick format blanks labels on spans under a decade.
- Take chart colors from the CSS variables in `global.css`, such as the `--chart-*` set, never from literal values, so dark mode needs no re-render. The series and emphasis hues were checked as a pair for lightness, chroma, color-vision separation, and contrast against the surface in both themes, so recheck both before changing either.
- Give every Plot chart a `TableTwin` with the same values, and keep tooltips to values the twin also shows. The static `Forest` is its own table twin.
- Draw score points through `forestMarks` on a domain from `forestDomain`, or from `sharedDomain` for several boards on one axis, so every point plot cuts and marks outliers by the same rule. Use each report's frozen method, rules and public identity map for closed rounds.

## The Vote Page

- The integration routes `/vote/` in every build. Its island fetches personalized data only at runtime.
- The island fetches pairs from the same-origin `/api` at runtime, so no vote data enters any build, and it never shows anything that identifies a system, including timing and server error details beyond the API's message.
- The voting runtime applies the policy that [Arena](../design/arena.md#vote-api) gives. Keep the page within it, render case and output Markdown only through `lib/markdown.ts`, and keep its images disabled as well, so output text cannot load a remote image.
- HTML outputs load by URL in `<iframe sandbox="allow-scripts" referrerPolicy="no-referrer">` from the configured separate render origin. Never use `srcdoc`, which inherits the vote page's policy.
