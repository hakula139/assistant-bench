# TypeScript

Conventions for the site under `web/` that the tooling doesn't already state.

## Enforced by Tooling

- `web/tsconfig.json` extends Astro's strict config, and `pnpm typecheck` runs `astro check` over every `.astro`, `.ts`, and `.tsx` file.
- `web/eslint.config.js` applies typescript-eslint's `strictTypeChecked` rules and the React hooks rules to TypeScript, and the Astro plugin's recommended rules to `.astro` files.
- The root `.prettierrc.json` formats `web/` with the Astro and Tailwind plugins, which sort class names, including those inside `cn()` and `cva()`.
- TypeScript stays on 6.x because typescript-eslint and `@astrojs/check` do not yet accept 7.

## Layout

```text
web/src/
├── components/boards/                      # The hydrated boards, plus the tables their pages share
├── components/charts/                      # Observable Plot charts and their table twins
├── components/scorers/                     # The scorer registry and each scorer's board, home panel, and methodology section
├── components/site/                        # Header, wordmark, family panel, static forest and its legend, method strip, glossary, term popovers, and shared page parts
├── components/ui/                          # shadcn/ui copies
├── components/vote/                        # The vote island
├── content.config.ts                       # The families, reports, systems, and models collections
├── env.d.ts                                # The shape of .astro modules for plain TypeScript and ESLint
├── integrations/local-vote.ts              # Injects the vote route when BENCH_LOCAL=1
├── layouts/                                # The page shell
├── lib/                                    # The report and family schemas, the system names, scorer steps and glossary, the forest axis, the site name, the vote API client, formatters, hooks
├── pages/                                  # Public routes only
├── styles/global.css                       # Tokens, fonts, and the shadcn/ui and chart variables
├── test/                                   # The fixture, JSON Schema, and chart stub helpers and the page tests
└── vote/vote.astro                         # The vote page, outside pages/ so only the integration routes it
```

## Reports

- The site reads only `families/*/family.toml` and `reports/*/*.json` through the `families` and `reports` collections' `glob()` loaders, `systems.toml` through the `systems` collection's `file()` loader, and the export that `BENCH_NIXOS_CONFIG` names through the `models` collection's inline loader, so `pnpm build` and `pnpm typecheck` run only inside the dev shell. It drops every family without `public = true` together with its reports. Nothing under the data directory reaches a build.
- `lib/family.ts` lists only the `family.toml` keys the site reads, with objects that drop the rest, since the pydantic settings model already rejects unknown keys.
- `lib/report.ts` mirrors the pydantic `Report` model field for field with strict Zod objects, so the build rejects a report with a field the schema lacks or a value of another type. Change both schemas in one PR. `pnpm test` parses both report fixtures and compares the Zod schema's JSON Schema with `tests/fixtures/report.schema.json`, which pytest writes from the pydantic models, within each results variant, over every field's types, nullability, constants and enums, and every object's required keys, allowed keys, and map key names, so a schema change on either side fails a check. Numeric bounds, formats, and descriptions are not compared.
- A new scorer adds a results variant to the schema, its entry in `scorerViews` in `components/scorers/index.ts` with its board, home panel, methodology section, and legend marks, its entry in `scorers` and steps in `methodSteps` in `lib/method.ts`, and its icons in `components/site/Method.tsx`. The home, family, and methodology pages dispatch only through `scorerViews`, and the family page links the section at `#<scorer>`. Icons and steps key on the scorer and the family's `render`, never on a family name. The type check fails until every record and switch handles the new variant, and `pnpm test` fails until the linked section exists.

## Components

- Add shadcn/ui components with `pnpm exec shadcn add <component>` from `web/`, then commit the copy. Adjust a copy in place when the site needs it, as `toggle.tsx` does for its pressed state and its `choice` variant for the verdicts, `progress.tsx` for its value and track, `table.tsx` for its `numeric` cells, and `badge.tsx` for its `status` variant.
- Style with the shadcn/ui components and their variants first, and with Tailwind utilities where none fits, keeping raw CSS to theme tokens in `global.css`. When several elements share a look, give it one home, such as a component variant, a component like `Section`, or a shared class constant like `note` in `components/site/PageParts.tsx`, so the class list is not repeated. Running text stays within `measure`, and page blocks sit `stack` apart, which `Base.astro` applies to every page's top level.
- Hydrate a component only where the page is interactive. Tables render as static React components, and island props stay serializable, which is why `DotRangePlot` takes a named format rather than a function.
- Pass filter state through `useSearchParam` so each filter lands in the URL.

## Charts

- Render every chart through `PlotFigure`, which sizes it to its container and measures labels in the chart's font. Size label margins from those measurements, never from a fixed width.
- Pick log-axis ticks with `logTicks` and label them through the axis mark's `text` option, because Plot's log tick format blanks labels on spans under a decade.
- Take chart colors from the CSS variables in `global.css`, such as the `--chart-*` set, never from literal values, so dark mode needs no re-render. The series and emphasis hues were checked as a pair for lightness, chroma, color-vision separation, and contrast against the surface in both themes, so recheck both before changing either.
- Give every Plot chart a `TableTwin` with the same values, and keep tooltips to values the twin also shows. The static `Forest` is a table whose rows carry their values as text and in a hover title, so it is its own twin.
- Draw score intervals through `forestMarks` on a domain from `forestDomain`, or from `sharedDomain` for several boards on one axis, so every forest plot cuts and marks outliers by the same rule.

## The Vote Page

- The page exists only in builds with `BENCH_LOCAL=1`, so the public site never routes it. Keep it out of `src/pages/`.
- The island fetches pairs from the same-origin `/api` at runtime, so no vote data enters any build, and it never shows anything that identifies a system, including timing and server error details beyond the API's message.
- `bench serve` sends everything under `/vote/` with the policy that [Arena](../design/arena.md#vote-api) gives. Keep the page within it, and keep Markdown images disabled as well, so the policy is not the only thing that stops an output from loading a remote image.
- HTML outputs load by URL in `<iframe sandbox="allow-scripts">` from the separate render origin. Never use `srcdoc`, which inherits the vote page's policy.
