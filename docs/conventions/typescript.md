# TypeScript

Conventions for the site under `web/` that the tooling doesn't already state.

## Enforced by Tooling

- `web/tsconfig.json` extends Astro's strict config, and `pnpm typecheck` runs `astro check` over every `.astro`, `.ts`, and `.tsx` file.
- `web/eslint.config.js` applies typescript-eslint's `strictTypeChecked` rules and the React hooks rules to TypeScript, and the Astro plugin's recommended rules to `.astro` files.
- The root `.prettierrc` formats `web/` with the Astro and Tailwind plugins, which sort class names, including those inside `cn()` and `cva()`.
- TypeScript stays on 6.x because typescript-eslint and `@astrojs/check` do not yet accept 7.

## Layout

```text
web/src/
├── components/boards/                      # One board per scorer, plus the tables its pages share
├── components/charts/                      # Observable Plot charts and their table twins
├── components/site/                        # Header, theme toggle, and other page chrome
├── components/ui/                          # shadcn/ui copies
├── components/vote/                        # The vote island
├── content.config.ts                       # The reports collection
├── integrations/local-vote.ts              # Injects the vote route when BENCH_LOCAL=1
├── layouts/                                # The page shell
├── lib/                                    # The report schema, the vote API client, formatters, hooks
├── pages/                                  # Public routes only
├── styles/global.css                       # Tokens, fonts, and the shadcn/ui and chart variables
├── test/                                   # Helpers shared by the Vitest tests
└── vote/vote.astro                         # The vote page, outside pages/ so only the integration routes it
```

## Reports

- The site reads only `reports/*/*.json`, through the `reports` collection's `glob()` loader. Nothing under the data directory reaches a build.
- `lib/report.ts` mirrors the pydantic `Report` model field for field with strict Zod objects, so a report that gains, loses, or retypes a field fails the build. Change both schemas in one PR. `pnpm test` parses `tests/fixtures/report.json`, which the pydantic models write, and fails when the Zod schema stops accepting it.
- A new scorer adds a results variant to the schema, a board under `components/boards/`, and an entry in the family page's per-scorer summaries, whose `Record` type fails the type check until the entry exists.

## Components

- Add shadcn/ui components with `pnpm exec shadcn add <component>` from `web/`, then commit the copy. Adjust a copy in place when the site needs it, as `toggle.tsx` does for its pressed state.
- Hydrate a component only where the page is interactive. Tables render as static React components, and island props stay serializable, which is why `DotRangePlot` takes a named format rather than a function.
- Pass filter state through `useSearchParam` so each filter lands in the URL.

## Charts

- Render every chart through `PlotFigure`, which sizes it to its container.
- Take chart colors from the CSS variables in `global.css`, such as the `--chart-*` set, never from literal values, so dark mode needs no re-render. The series, emphasis, and sequential steps were checked as a set for lightness, chroma, color-vision separation, and contrast in both themes, so recheck the whole set before changing any of them.
- Give every chart a `TableTwin` with the same values, and keep tooltips to values the twin also shows.

## The Vote Page

- The page exists only in builds with `BENCH_LOCAL=1`, so the public site never routes it. Keep it out of `src/pages/`.
- The island fetches pairs from the same-origin `/api` at runtime, so no vote data enters any build, and it never shows anything that identifies a system, including timing and server error details beyond the API's message.
- HTML outputs load by URL in `<iframe sandbox="allow-scripts">` from the separate render origin. Never use `srcdoc`, which inherits the vote page's policy.
