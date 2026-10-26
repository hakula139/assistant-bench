import { getContainerRenderer } from '@astrojs/react/container-renderer';
import { loadRenderers } from 'astro:container';
import { experimental_AstroContainer as AstroContainer } from 'astro/container';
import { JSDOM } from 'jsdom';
import { describe, expect, test, vi } from 'vitest';

import FamilyPanel from '@/components/site/FamilyPanel.astro';
import Header from '@/components/site/Header.astro';
import { familyTitle } from '@/lib/aspects';
import type { Defect } from '@/lib/defects';
import type { FamilyConfig, VotesConfig } from '@/lib/family';
import { defectTerms, terms } from '@/lib/glossary';
import { reportSchema } from '@/lib/report';
import {
  boardPath,
  familyPaths,
  roundLinks,
  roundPath,
  roundPaths,
  systemPath,
  systemPaths,
  type Family,
} from '@/lib/reports';
import { REPOSITORY } from '@/lib/site';
import SystemPage from '@/pages/[aspect]/[board]/[system].astro';
import FamilyPage from '@/pages/[aspect]/[board]/index.astro';
import RoundPage from '@/pages/[aspect]/[board]/rounds/[round].astro';
import HomePage from '@/pages/index.astro';
import MethodologyPage from '@/pages/methodology.astro';
import { FIXTURE_NAMES, readFixture, shownSlugs } from '@/test/fixture';

const listed = vi.hoisted(() => ({ families: [] as Family[] }));
vi.mock(import('@/lib/reports'), async (importOriginal) => ({
  ...(await importOriginal()),
  getFamilies: () => Promise.resolve(listed.families),
}));

const SLUGS = {
  ...FIXTURE_NAMES,
  'writing-arena': 'Arena Writing',
  'writing-smoke': 'Smoke Tasks',
  'coding-repo': 'Repository Tasks',
};

const arenaConfig: VotesConfig = {
  scorer: 'votes',
  aspect: 'writing',
  display_name: 'Arena Writing',
  systems: ['acme-large', 'acme-new', 'acme-pro', 'acme-small'],
  reference: 'acme-large',
  render: 'text',
  language: 'zh',
  gates: { min_chars: 40, max_chars: 6000, script: 'hans' },
  public: true,
};

async function family(): Promise<Family> {
  const latest = reportSchema.parse(await readFixture());
  const earlier = structuredClone(latest);
  earlier.round = '2026-10-07';
  earlier.closed_at = '2026-10-07T18:00:00Z';
  if (earlier.results.scorer !== 'votes' || latest.results.scorer !== 'votes') {
    throw new Error('the fixture is a votes report');
  }
  for (const standing of earlier.results.standings) {
    standing.score = 900;
  }
  for (const report of [earlier, latest]) {
    for (const metadata of Object.values(report.systems)) {
      metadata.fingerprint = `${report.round.replaceAll('-', '')}abcdef01`;
    }
  }
  return listedFamily('writing-arena', arenaConfig, [earlier, latest]);
}

async function codingFamily(): Promise<Family> {
  const config: VotesConfig = {
    ...arenaConfig,
    aspect: 'coding',
    display_name: 'Repository Tasks',
    render: 'diff',
    language: undefined,
    gates: {},
    harness: 'terminus-2',
    max_turns: 40,
  };
  return listedFamily('coding-repo', config, (await family()).rounds);
}

async function ledgerFamily(): Promise<Family> {
  const closed = reportSchema.parse(await readFixture('report-ledger'));
  const config: VotesConfig = {
    scorer: 'votes',
    aspect: 'writing',
    display_name: 'Synthetic writing',
    systems: ['large', 'small'],
    reference: 'large',
    render: 'text',
    gates: {},
    public: true,
  };

  return { ...listedFamily(closed.family, config, [closed]), systemNames: {} };
}

function headerStats(html: string): string[] {
  return [...new JSDOM(html).window.document.querySelectorAll('header dl dt')].map(
    (label) => `${label.textContent} ${label.nextElementSibling?.textContent ?? ''}`,
  );
}

function listedFamily(name: string, config: FamilyConfig, rounds: Family['rounds']): Family {
  if (!config.public || config.aspect === undefined) {
    throw new Error(`family ${name} is not listed under a public aspect`);
  }
  return {
    name,
    title: familyTitle(config),
    aspect: config.aspect,
    config,
    rounds,
    systemNames: FIXTURE_NAMES,
  };
}

async function render(
  page: Parameters<AstroContainer['renderToString']>[0],
  props: Record<string, unknown>,
  path = '/',
): Promise<string> {
  const container = await AstroContainer.create({
    renderers: await loadRenderers([getContainerRenderer()]),
  });
  return container.renderToString(page, {
    props,
    request: new Request(new URL(path, 'https://bench.test')),
  });
}

const text = (html: string) =>
  html
    .replace(/<[^>]+>/g, ' ')
    .replace(/&#(?:x27|39);/g, "'")
    .replace(/\s+/g, ' ');

function testsFamily(): Family {
  const report = reportSchema.parse({
    family: 'writing-smoke',
    round: '2026-10-21',
    closed_at: '2026-10-21T18:00:00Z',
    systems: Object.fromEntries(
      ['acme-small', 'acme-pro'].map((system) => [
        system,
        { display_name: FIXTURE_NAMES[system], fingerprint: null },
      ]),
    ),
    results: {
      scorer: 'tests',
      standings: [
        {
          system: 'acme-small',
          trials: 4,
          errored: 0,
          pass_rate: 0.25,
          mean_cost_usd: null,
          p50_latency_sec: null,
        },
        {
          system: 'acme-pro',
          trials: 4,
          errored: 1,
          pass_rate: 0.75,
          mean_cost_usd: null,
          p50_latency_sec: null,
        },
      ],
    },
  });
  return listedFamily(
    'writing-smoke',
    {
      scorer: 'tests',
      aspect: 'writing',
      display_name: 'Smoke Tasks',
      systems: ['acme-pro', 'acme-small'],
      public: true,
    },
    [report],
  );
}

function guide(html: string): string {
  const match = /href="\/methodology\/#([\w-]+)"/.exec(html);
  if (!match) {
    throw new Error('the family page links no methodology section');
  }
  return match[1];
}

/** Every root-relative page link, leaving out the home page, its anchors, and the methodology. */
function boardLinks(html: string): string[] {
  const document = new JSDOM(html).window.document;

  return [...document.querySelectorAll<HTMLAnchorElement>('a[href^="/"]')]
    .filter((link) => link.closest('nav') === null)
    .map((link) => link.getAttribute('href') ?? '')
    .filter((href) => !href.startsWith('/methodology/') && !/\.\w+$/.test(href));
}

function stepHints(html: string): string[] {
  return [...html.matchAll(/<ol aria-label="\w+ scoring steps"[\s\S]*?<\/ol>/g)].flatMap(
    ([strip]) =>
      [...strip.matchAll(/<button[^>]*aria-haspopup="dialog"[^>]*>([^<]*)<\/button>/g)].map(
        ([, label]) => label,
      ),
  );
}

function island(html: string, name: string): Element | null {
  return new JSDOM(html).window.document.querySelector(`astro-island[component-export="${name}"]`);
}

function islandHints(html: string, name: string): (string | null)[] {
  return [...(island(html, name)?.querySelectorAll('[aria-haspopup="dialog"]') ?? [])].map(
    (hint) => hint.textContent,
  );
}

describe('family page', () => {
  test('renders a genuine closed ledger board without current catalog labels', async () => {
    const closed = await ledgerFamily();

    const html = await render(FamilyPage, { family: closed, boards: [] });

    expect(text(html)).toContain('Small');
    expect(text(html)).toContain('Large');
    expect(text(html)).toContain('Effective vote mass 120');
    expect(text(html)).not.toContain('No round has closed yet.');
  });

  test('links each system to its page under the board', async () => {
    const arena = await family();

    const html = await render(FamilyPage, { family: arena, boards: [] });

    expect(boardLinks(html)).toContain(systemPath(arena, 'acme-pro'));
    expect(boardLinks(html).filter((href) => !href.startsWith(boardPath(arena)))).toEqual([]);
  });

  test("draws the family's steps, then the round summary", async () => {
    const html = await render(FamilyPage, { family: await family(), boards: [] });

    expect(text(html)).toContain(
      'Run same cases for all Gates failing outputs lose Blind pairs system names hidden Scores ranked from votes',
    );
    expect(stepHints(html)).toEqual([
      'same cases for all',
      'failing outputs lose',
      'system names hidden',
      'ranked from votes',
    ]);
    expect(new JSDOM(html).window.document.querySelector('dl dd')?.textContent).toBe(
      '340 over 10 sessions, 20 left out',
    );
    expect(text(html)).toContain('1 Acme Pro 1,004 114');
  });

  test('lists systems as unmeasured before the first round closes', async () => {
    const empty: Family = { ...(await family()), rounds: [] };

    const html = await render(FamilyPage, { family: empty, boards: [] });

    expect(text(html)).toContain('No round has closed yet.');
    expect(text(html)).toContain('Acme Large unmeasured');
    expect(text(html)).toContain('Acme Pro no votes');
    expect(text(html)).not.toMatch(/\bLeader\b/);
  });

  test('keys the empty board to the marks it draws', async () => {
    const empty: Family = { ...(await family()), rounds: [] };

    const html = await render(FamilyPage, { family: empty, boards: [] });

    expect(new JSDOM(html).window.document.querySelector('ul[aria-label="Legend"]')).toBeNull();
    expect(html).not.toContain('Score, 95% interval');
    expect(islandHints(html, 'Forest')).toEqual(['unmeasured', 'no votes', 'no votes', 'no votes']);
  });

  test.each([
    ['family page', FamilyPage, { boards: [] }],
    ['family panel', FamilyPanel, {}],
  ])("names a tests family's systems on the %s before its first round", async (_, page, props) => {
    const empty: Family = { ...testsFamily(), rounds: [] };
    const none: Family = { ...empty, config: { ...empty.config, systems: [] } };

    const [html, noneHtml] = await Promise.all(
      [empty, none].map((loaded) => render(page, { family: loaded, ...props })),
    );

    expect(text(html)).toContain('Systems: Acme Pro, Acme Small');
    expect(text(noneHtml)).toContain('Systems: –');
  });

  test('draws the run of a family that renders pages as a page', async () => {
    const pages: Family = {
      ...(await family()),
      config: { ...arenaConfig, render: 'html' },
      rounds: [],
    };

    const html = await render(FamilyPage, { family: pages, boards: [] });

    expect(html).toContain('lucide-app-window');
  });

  test("draws a tests family's steps and its pass rates", async () => {
    const html = await render(FamilyPage, { family: testsFamily(), boards: [] });

    expect(text(html)).toContain(
      "Run same tasks for all Tests each task's own tests Pass rate share of trials passed",
    );
    expect(stepHints(html)).toHaveLength(3);
    expect(text(html)).toContain('Acme Pro 75% 4 1');
  });

  test("draws a coding family's steps and names its gate-failure column for the tests", async () => {
    const html = await render(FamilyPage, { family: await codingFamily(), boards: [] });

    expect(text(html)).toContain(
      'Run same tasks for all Tests failing solutions lose Blind pairs system names hidden Scores ranked from votes',
    );
    expect(html).toContain('lucide-file-diff');
    expect(stepHints(html)).toHaveLength(4);
    expect(text(html)).toContain(`${terms.failedTests.term} What ${terms.failedTests.term} means`);
    expect(text(html)).not.toContain(terms.gateFailures.term);
  });

  test.each([
    ['with rounds', family],
    ['before its first round', async () => ({ ...(await family()), rounds: [] })],
    ['scored by tests', () => Promise.resolve(testsFamily())],
    ['on coding', codingFamily],
  ])('names the family and its systems by display name %s', async (_, load) => {
    const loaded = await load();

    const html = await render(FamilyPage, { family: loaded, boards: [] });

    expect(html).toContain(`<title>${loaded.title} | HAKULA†BENCH</title>`);
    expect(text(html)).toContain(`${loaded.title} Run `);
    expect(shownSlugs(html, SLUGS)).toEqual([]);
  });

  test.each([
    ['votes', family, 'votes', 'Cases', 'Ranked by blind pairwise votes'],
    ['tests', () => Promise.resolve(testsFamily()), 'tests', 'Tasks', 'Scored by automated tests'],
    [
      'coding',
      codingFamily,
      'tests-then-votes',
      'Tasks',
      'Ranked by hidden tests, then blind pairwise votes',
    ],
  ])(
    'describes the %s board and links its methodology section and its inputs',
    async (_, load, section, inputs, summary) => {
      const loaded = await load();
      const methodology = await render(MethodologyPage, {});

      const html = await render(FamilyPage, { family: loaded, boards: [] });

      const document = new JSDOM(html).window.document;
      const href = `${REPOSITORY}/tree/main/families/${loaded.name}`;
      expect(guide(html)).toBe(section);
      expect(methodology).toContain(`id="${section}"`);
      expect(document.querySelector(`a[href="${href}"]`)?.textContent.trim()).toBe(inputs);
      expect(document.querySelector('meta[name="description"]')?.getAttribute('content')).toBe(
        `${loaded.title}: ${summary}.`,
      );
    },
  );
});

describe('methodology page', () => {
  test("draws each scorer's steps without a family's hints", async () => {
    const html = await render(MethodologyPage, {});

    expect(text(html)).toContain('Run same tasks for all');
    expect(stepHints(html)).toEqual([]);
  });

  test('states unavailable population inference and opens the example points', async () => {
    const html = await render(MethodologyPage, {});

    expect(text(html)).toContain('Uncertainty for new cases and voters is unavailable');
    expect(islandHints(html, 'Forest')).toEqual(['1,040', '930', '380', 'no votes']);
  });

  test('defines the terms a board of diffs uses in its own section', async () => {
    const html = await render(MethodologyPage, {});

    const section = new JSDOM(html).window.document.getElementById(
      'tests-then-votes',
    )?.parentElement;
    const rows = [...(section?.querySelectorAll('tbody tr') ?? [])].map((row) =>
      [...row.querySelectorAll('td')].map((cell) => cell.textContent),
    );
    expect(rows).toEqual(
      [terms.failedTests, terms.solutionCostLatency, terms.diffLengthBias].map((term) => [
        term.term,
        term.definition,
      ]),
    );
  });

  test("keeps the last words of a glossary term's meaning on one line", async () => {
    const html = await render(MethodologyPage, {});

    const row = [
      ...new JSDOM(html).window.document.querySelectorAll<HTMLTableRowElement>('tbody tr'),
    ].find((item) => item.cells[0].textContent === terms.bothBad.term);
    expect(row?.cells[1].textContent).toBe(terms.bothBad.definition);
    expect(row?.cells[1].querySelector('.text-nowrap')?.textContent).toBe('both bad.');
  });
});

describe('home page', () => {
  test("keys the shared legend to the marks the families' panels draw", async () => {
    listed.families = [{ ...(await family()), rounds: [] }, testsFamily()];

    const html = await render(HomePage, {});

    const legend = /<ul aria-label="Legend"[^>]*>([\s\S]*?)<\/ul>/.exec(html)?.[1] ?? '';
    expect(text(legend).trim()).toBe('');
  });

  test("titles each aspect's section with its icon and the anchor its breadcrumbs link", async () => {
    listed.families = [await codingFamily(), await family()];

    const html = await render(HomePage, {});

    const headings = [...new JSDOM(html).window.document.querySelectorAll('h2')].map((heading) => [
      heading.id,
      heading.querySelector('svg')?.classList[1],
      heading.textContent,
    ]);
    expect(headings).toEqual([
      ['writing', 'lucide-pen-line', 'Writing'],
      ['coding', 'lucide-square-terminal', 'Coding'],
    ]);
  });
});

describe('family panel', () => {
  test('links its short name and its systems to pages under its board', async () => {
    const arena = await family();

    const html = await render(FamilyPanel, { family: arena });

    expect(boardLinks(html)).toContain(boardPath(arena));
    expect(boardLinks(html)).toContain(systemPath(arena, 'acme-pro'));
    expect(boardLinks(html).filter((href) => !href.startsWith(boardPath(arena)))).toEqual([]);
  });

  test("draws the latest round's points and states unavailable inference", async () => {
    const html = await render(FamilyPanel, { family: await family() });

    const leader = new JSDOM(html).window.document.querySelector('[aria-label="Leader"]');

    expect(text(html)).toContain('Votes: Round 2026-10-21 · 340 votes');
    expect(text(html)).toContain('Acme Pro 1,004');
    expect(leader).toBeNull();
    expect(text(html)).toContain('Uncertainty for new cases and voters is unavailable');
    expect(islandHints(html, 'Leader')).toEqual([]);
  });

  test("gives each row's point score a hint", async () => {
    const html = await render(FamilyPanel, { family: await family() });

    expect(islandHints(html, 'Forest')).toContain('1,004');
  });

  test('keeps only the report\x27s unranked systems after the round', async () => {
    const html = await render(FamilyPanel, { family: await family() });

    expect(text(html)).toContain('Acme Edge unranked');
    expect(text(html)).not.toContain('Acme New');
  });

  test.each([
    ['with rounds', family],
    ['before its first round', async () => ({ ...(await family()), rounds: [] })],
    ['scored by tests', () => Promise.resolve(testsFamily())],
  ])('names the family and its systems by display name %s', async (_, load) => {
    const loaded = await load();

    const html = await render(FamilyPanel, { family: loaded });

    expect(text(html)).toContain(`${loaded.config.display_name} `);
    expect(shownSlugs(html, SLUGS)).toEqual([]);
  });

  test("lists a tests family's systems by pass rate, best first", async () => {
    const html = await render(FamilyPanel, { family: testsFamily() });

    expect(text(html)).toContain('System Pass rate Acme Pro 75% Acme Small 25%');
  });
});

describe('system page', () => {
  test('renders weighted rates from a genuine closed ledger without current labels', async () => {
    const closed = await ledgerFamily();

    const html = await render(SystemPage, { family: closed, system: 'large', boards: [] });

    expect(text(html)).toContain('Win rates use the recorded actor weights.');
    expect(text(html)).toContain('Counts show raw ordinary votes.');
    expect(text(html)).toContain('58%');
    expect(text(html)).not.toContain('57%');
    expect(html).toContain('<title>Large on Writing · Synthetic writing | HAKULA†BENCH</title>');
  });

  test('renders closed labels after runnable entries retire or change their names', async () => {
    const arena = await family();
    arena.systemNames = {};
    arena.rounds[0].systems['acme-pro'].display_name = 'Historical Pro';
    arena.rounds[1].systems['acme-pro'].display_name = 'Current Pro';

    const html = await render(SystemPage, { family: arena, system: 'acme-pro', boards: [] });

    expect(html).toContain('<title>Current Pro on Writing · Arena Writing | HAKULA†BENCH</title>');
    expect(html).toContain('Historical Pro');
    expect(islandHints(html, 'RoundsTable')).toContain('2026-10-07');
    expect(shownSlugs(html, SLUGS)).toEqual([]);
  });

  test.each([
    [
      'cases',
      family,
      "Output length and latency Each system's median and 10th to 90th percentile range.",
    ],
    [
      'diffs',
      codingFamily,
      "Output tokens and latency Each system's median and 10th to 90th percentile range, where latency spans the agent's whole run.",
    ],
  ])("titles the token and latency ranges of a family's %s", async (_, load, heading) => {
    const html = await render(SystemPage, { family: await load(), system: 'acme-pro', boards: [] });

    expect(text(html)).toContain(heading);
  });

  test("summarizes the system's own standing in its latest round", async () => {
    const html = await render(SystemPage, {
      family: await family(),
      system: 'acme-pro',
      boards: [],
    });

    expect(text(html)).toContain('Writing Arena Writing Acme Pro Score');
    expect(headerStats(html)).toEqual([
      'Score 1,004',
      'Rank 1',
      'Votes 114 (89 decisive)',
      'Round 2026-10-21',
    ]);
    expect(islandHints(html, 'VotesStat')).toEqual(['(89 decisive)']);
  });

  test('names the system, its family, and every other system by display name', async () => {
    const html = await render(SystemPage, {
      family: await family(),
      system: 'acme-pro',
      boards: [],
    });

    expect(html).toContain('<title>Acme Pro on Writing · Arena Writing | HAKULA†BENCH</title>');
    expect(shownSlugs(html, SLUGS)).toEqual([]);
  });

  test("opens each round's fingerprint from its date in the rounds table", async () => {
    const html = await render(SystemPage, {
      family: await family(),
      system: 'acme-pro',
      boards: [],
    });

    expect(islandHints(html, 'RoundsTable')).toEqual(['2026-10-21', '1,004', '2026-10-07', '900']);
  });

  test("counts each issue tagged on the system's outputs, with its share and a hint on each issue", async () => {
    const prose: Defect[] = [
      'scope',
      'facts',
      'clarity',
      'tone',
      'mechanics',
      'formulaic',
      'length',
    ];
    const html = await render(SystemPage, {
      family: await family(),
      system: 'acme-mini',
      boards: [],
    });

    expect(text(html)).toContain(
      "Issues Votes that tagged this system's output with each issue. Issue Votes Share Misses the brief 0 0% Wrong facts 3 2% Hard to follow 0 0% Wrong tone 4 3% Language errors 4 3% Formulaic 0 0% Too long 1 1%",
    );
    expect(islandHints(html, 'DefectsTable')).toEqual(
      prose.map((defect) => defectTerms[defect].term),
    );
  });

  test.each([
    [
      'in the order the report keys them',
      { testing: 2, scope: 1 },
      'Weak tests 2 2% Misses the brief 1 1%',
    ],
    ['nowhere for a family that tags none', {}, null],
  ])('lists the issues %s', async (_, defects, row) => {
    const tagged = await family();
    const latest = tagged.rounds[tagged.rounds.length - 1];
    if (latest.results.scorer !== 'votes') {
      throw new Error('the fixture is a votes report');
    }
    const mini = latest.results.standings.find((standing) => standing.system === 'acme-mini');
    if (mini === undefined) {
      throw new Error('the fixture ranks acme-mini');
    }
    mini.defects = defects;

    const html = await render(SystemPage, { family: tagged, system: 'acme-mini', boards: [] });

    if (row === null) {
      expect(text(html)).not.toContain('Issues Votes that tagged');
      expect(island(html, 'DefectsTable')).toBeNull();
    } else {
      expect(text(html)).toContain(`Issue Votes Share ${row} Rounds`);
    }
  });

  test('passes the rounds table only the fields it shows', async () => {
    const html = await render(SystemPage, {
      family: await family(),
      system: 'acme-pro',
      boards: [],
    });

    const rounds = island(html, 'RoundsTable');
    const props = JSON.parse(rounds?.getAttribute('props') ?? '{}') as {
      appearances: [number, [number, Record<string, unknown>][]];
    };
    expect(props.appearances[1].map(([, row]) => Object.keys(row).sort())).toEqual([
      ['anchor', 'method', 'reference', 'round', 'rules', 'standing', 'systems'],
      ['anchor', 'method', 'reference', 'round', 'rules', 'standing', 'systems'],
    ]);
  });

  test('marks a round whose anchor differs from the reference', async () => {
    const arena = await family();
    const earlier = arena.rounds[0].results;
    if (earlier.scorer !== 'votes') {
      throw new Error('the fixture is a votes report');
    }
    earlier.anchor = 'acme-pro';

    const html = await render(SystemPage, {
      family: arena,
      system: 'acme-pro',
      boards: [],
    });

    expect(text(html)).toContain('Round Score Votes');
    expect(text(html)).toMatch(/2026-10-07 Anchor/);
    expect(text(html)).not.toMatch(/2026-10-21 Anchor/);
  });

  test('leaves the round unmarked when every round has one anchor', async () => {
    const html = await render(SystemPage, {
      family: await family(),
      system: 'acme-pro',
      boards: [],
    });

    expect(text(html)).toContain('Round Score Votes');
    expect(text(html)).not.toContain('Anchor');
  });

  test('leaves out the head-to-head chart for a system without votes', async () => {
    const html = await render(SystemPage, {
      family: await family(),
      system: 'acme-tiny',
      boards: [],
    });

    expect(headerStats(html).slice(0, 3)).toEqual(['Score 233', 'Rank 6', 'Votes 0 (0 decisive)']);
    expect(html).not.toContain('Head to head');
    expect(text(html)).toContain(
      'Issue Votes Share Misses the brief 0 – Wrong facts 0 – Hard to follow 0 – Wrong tone 0 – Language errors 0 –',
    );
    expect(islandHints(html, 'StatusMark')).toEqual(['Preliminary']);
  });

  test("links the family's aspect on the home page, then the family's board", async () => {
    const html = await render(SystemPage, {
      family: await family(),
      system: 'acme-pro',
      boards: [],
    });

    expect(navLinks(html, 'breadcrumb')).toEqual([
      ['/#writing', 'Writing', null],
      ['/writing/arena/', 'Arena Writing', null],
    ]);
  });
});

const classesOf = (element: Element | null) => element?.getAttribute('class') ?? '';

/** The blocks of a stack as they lay out, looking through the `display: contents` islands. */
function blocks(stack: Element): Element[] {
  return [...stack.children].flatMap((child) =>
    child.localName === 'astro-island' ? [...child.children] : [child],
  );
}

const sources = import.meta.glob<string>(['/src/**/*.{astro,tsx}', '!/src/components/ui/**'], {
  query: '?raw',
  import: 'default',
  eager: true,
});

describe('stacks', () => {
  test('use gap in every page and component, since margins skip the islands', () => {
    const files = Object.entries(sources);
    expect(files.length).toBeGreaterThan(20);

    const spaced = files.filter(([, source]) => /\bspace-[xy]-/.test(source)).map(([path]) => path);

    expect(spaced).toEqual([]);
  });

  test.each([
    ['family page', FamilyPage],
    ['system page', SystemPage],
  ])('space the islands on the %s with gap', async (_, page) => {
    const html = await render(page, { family: await family(), system: 'acme-pro', boards: [] });
    // An island inside a run of text, such as a hint in a stat's detail, flows with the text.

    const parents = [...new JSDOM(html).window.document.querySelectorAll('astro-island')]
      .map((island) => island.parentElement)
      .filter((parent) => parent?.localName !== 'span')
      .map(classesOf);
    expect(parents).not.toEqual([]);

    expect(parents.filter((classes) => !/\bgap-/.test(classes))).toEqual([]);
  });

  test.each([
    ['votes family page', FamilyPage, family],
    ['tests family page', FamilyPage, () => Promise.resolve(testsFamily())],
    ['system page', SystemPage, family],
  ])(
    'keep vertical margins off the blocks of each stack on the %s',
    async (_, page, makeFamily) => {
      const html = await render(page, {
        family: await makeFamily(),
        system: 'acme-pro',
        boards: [],
      });
      const stacks = [
        ...new JSDOM(html).window.document.querySelectorAll('.flex-col[class*="gap-"]'),
      ];
      expect(stacks).not.toEqual([]);

      const margined = stacks
        .flatMap(blocks)
        .map(classesOf)
        .filter((classes) => /(^|[\s:])-?m[tby]-/.test(classes));

      expect(margined).toEqual([]);
    },
  );
});

/** The `[href, label, aria-current]` of each link in the nav that `label` names. */
function navLinks(html: string, label: string): [string, string, string | null][] {
  const nav = new RegExp(`<nav\\b[^>]*aria-label="${label}"[^>]*>([\\s\\S]*?)</nav>`).exec(
    html,
  )?.[1];
  if (nav === undefined) {
    return [];
  }
  return [...nav.matchAll(/<a\b([^>]*)>([^<]*)<\/a>/g)].map(([, attributes, label]) => [
    /href="([^"]+)"/.exec(attributes)?.[1] ?? '',
    label.trim(),
    /aria-current="([^"]+)"/.exec(attributes)?.[1] ?? null,
  ]);
}

function propsAt<Props>(
  paths: { params: Record<string, string>; props: Props }[],
  params: Record<string, string>,
): Props {
  const path = paths.find((item) => JSON.stringify(item.params) === JSON.stringify(params));
  if (path === undefined) {
    throw new Error(`no page at ${JSON.stringify(params)}`);
  }
  return path.props;
}

describe('header', () => {
  test.each([0, 3])('keeps the same links with %i families listed', async (count) => {
    const arena = await family();
    listed.families = Array.from({ length: count }, (_, index) =>
      listedFamily(
        `writing-arena-${index}`,
        { ...arenaConfig, display_name: `Arena ${index}` },
        arena.rounds,
      ),
    );

    const html = await render(HomePage, {});

    expect(navLinks(html, 'Main')).toEqual([
      ['/', 'Leaderboards', 'page'],
      ['/methodology/', 'Methodology', null],
      ['/vote/', 'Vote', null],
    ]);
  });

  test('marks Leaderboards as the section of family and system pages', async () => {
    const arena = await family();

    const pages = [
      await render(FamilyPage, { family: arena, boards: [] }, '/writing/arena/'),
      await render(
        SystemPage,
        { family: arena, system: 'acme-pro', boards: [] },
        '/writing/arena/acme-pro/',
      ),
    ];

    for (const html of pages) {
      expect(navLinks(html, 'Main')).toEqual([
        ['/', 'Leaderboards', 'true'],
        ['/methodology/', 'Methodology', null],
        ['/vote/', 'Vote', null],
      ]);
    }
  });

  test.each(['methodology', 'vote'] as const)(
    'marks the %s link as the current page',
    async (section) => {
      const html = await render(Header, { section }, `/${section}/`);

      expect(navLinks(html, 'Main')).toEqual([
        ['/', 'Leaderboards', null],
        ['/methodology/', 'Methodology', section === 'methodology' ? 'page' : null],
        ['/vote/', 'Vote', section === 'vote' ? 'page' : null],
      ]);
    },
  );
});

describe('board switcher', () => {
  // Three boards of one aspect, where the empty one has no system pages yet.
  async function boards(): Promise<Family[]> {
    const arena = await family();
    return [
      arena,
      listedFamily(
        'writing-arena-en',
        { ...arenaConfig, display_name: 'Arena English' },
        arena.rounds,
      ),
      listedFamily('writing-arena-ja', { ...arenaConfig, display_name: 'Arena Japanese' }, []),
    ];
  }

  test("links the aspect's boards by short name and marks the current one", async () => {
    const families = await boards();

    const html = await render(
      FamilyPage,
      propsAt(familyPaths(families), { aspect: 'writing', board: 'arena-en' }),
    );

    expect(navLinks(html, 'Writing boards')).toEqual([
      ['/writing/arena/', 'Arena Writing', null],
      ['/writing/arena-en/', 'Arena English', 'page'],
      ['/writing/arena-ja/', 'Arena Japanese', null],
    ]);
    expect(new JSDOM(html).window.document.querySelector('summary')?.textContent).toBe(
      '3 Writing boards',
    );
  });

  test('links the system on each sibling board that gives it a page', async () => {
    const families = await boards();

    const html = await render(
      SystemPage,
      propsAt(systemPaths(families), {
        aspect: 'writing',
        board: 'arena',
        system: 'acme-pro',
      }),
    );

    expect(navLinks(html, 'Writing boards')).toEqual([
      ['/writing/arena/acme-pro/', 'Arena Writing', 'page'],
      ['/writing/arena-en/acme-pro/', 'Arena English', null],
    ]);
  });

  test('stays off a page whose aspect lists one board', async () => {
    const arena = await family();

    const html = await render(
      FamilyPage,
      propsAt(familyPaths([arena]), { aspect: 'writing', board: 'arena' }),
    );

    expect(navLinks(html, 'Writing boards')).toEqual([]);
  });
});

/** The ID of each closed round serialized into the page's islands, as an object with results. */
function islandRounds(html: string): string[] {
  const rounds: string[] = [];
  const walk = (value: unknown) => {
    if (Array.isArray(value)) {
      value.forEach(walk);
    } else if (typeof value === 'object' && value !== null) {
      // Astro serializes each prop as a `[type, value]` pair, where type 0 is a plain value.
      if ('results' in value && 'round' in value && Array.isArray(value.round)) {
        rounds.push(String(value.round[1]));
      }
      Object.values(value).forEach(walk);
    }
  };

  for (const element of new JSDOM(html).window.document.querySelectorAll('astro-island')) {
    walk(JSON.parse(element.getAttribute('props') ?? '{}'));
  }

  return rounds;
}

/** A tests family with an earlier round before the one `testsFamily` closes. */
function testsFamilyWithHistory(): Family {
  const tested = testsFamily();
  const [latest] = tested.rounds;
  const earlier = { ...structuredClone(latest), round: '2026-10-07' };
  earlier.closed_at = '2026-10-07T18:00:00Z';
  return { ...tested, rounds: [earlier, latest] };
}

/** Every board and round page that the routes build for a family, by path. */
async function boardPages(loaded: Family): Promise<Map<string, string>> {
  const pages = [
    ...familyPaths([loaded]).map(({ params, props }) => ({
      path: `/${params.aspect}/${params.board}/`,
      page: FamilyPage,
      props,
    })),
    ...roundPaths([loaded]).map(({ params, props }) => ({
      path: `/${params.aspect}/${params.board}/rounds/${params.round}/`,
      page: RoundPage,
      props,
    })),
  ];

  return new Map(
    await Promise.all(
      pages.map(async ({ path, page, props }) => [path, await render(page, props, path)] as const),
    ),
  );
}

describe('round pages', () => {
  test.each([
    ['votes', family, '/writing/arena/'],
    ['tests', () => Promise.resolve(testsFamilyWithHistory()), '/writing/smoke/'],
  ])(
    'give each closed round of a %s family a page that serializes only that round',
    async (_, load, board) => {
      const loaded = await load();

      const pages = await boardPages(loaded);

      expect([...pages].map(([path, html]) => [path, islandRounds(html)])).toEqual([
        [board, ['2026-10-21']],
        [`${board}rounds/2026-10-07/`, ['2026-10-07']],
        [`${board}rounds/2026-10-21/`, ['2026-10-21']],
      ]);
    },
  );

  test('links each round in the Round filter to a page that shows it, newest first', async () => {
    const arena = await family();
    const pages = await boardPages(arena);

    const links = roundLinks(arena);

    expect(links.map(({ round }) => round)).toEqual(['2026-10-21', '2026-10-07']);
    for (const { round, href } of links) {
      expect(islandRounds(pages.get(href) ?? ''), href).toEqual([round]);
    }
  });

  test("titles an earlier round's page by its round and links the latest from it", async () => {
    const arena = await family();

    const pages = await boardPages(arena);

    const notes = [...pages].map(([path, html]) => {
      const document = new JSDOM(html).window.document;
      const note = [...document.querySelectorAll('header p')].find((item) =>
        item.textContent.includes('earlier round'),
      );
      return [
        path,
        document.title,
        note?.textContent ?? null,
        note?.querySelector('a')?.getAttribute('href') ?? null,
      ];
    });
    expect(notes).toEqual([
      ['/writing/arena/', 'Writing · Arena Writing | HAKULA†BENCH', null, null],
      [
        '/writing/arena/rounds/2026-10-07/',
        'Writing · Arena Writing, round 2026-10-07 | HAKULA†BENCH',
        'This is an earlier round. The latest is 2026-10-21.',
        '/writing/arena/',
      ],
      [
        '/writing/arena/rounds/2026-10-21/',
        'Writing · Arena Writing, round 2026-10-21 | HAKULA†BENCH',
        null,
        null,
      ],
    ]);
  });

  test("draws the earlier round's own scores on its page", async () => {
    const arena = await family();

    const pages = await boardPages(arena);

    expect(text(pages.get('/writing/arena/rounds/2026-10-07/') ?? '')).toContain('Acme Pro 900');
    expect(text(pages.get('/writing/arena/') ?? '')).toContain('Acme Pro 1,004');
  });

  test("links a system page's round to the page of the round it shows", async () => {
    const arena = await family();
    const pages = await boardPages(arena);

    const html = await render(SystemPage, { family: arena, system: 'acme-pro', boards: [] });

    const href = new JSDOM(html).window.document.querySelector('header dl a')?.getAttribute('href');
    expect(href).toBe(roundPath(arena, '2026-10-21'));
    expect(islandRounds(pages.get(href ?? '') ?? '')).toEqual(['2026-10-21']);
  });
});
