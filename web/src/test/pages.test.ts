import { getContainerRenderer } from '@astrojs/react/container-renderer';
import { experimental_AstroContainer as AstroContainer } from 'astro/container';
import { loadRenderers } from 'astro:container';
import { JSDOM } from 'jsdom';
import { describe, expect, test, vi } from 'vitest';

import FamilyPanel from '@/components/site/FamilyPanel.astro';
import Header from '@/components/site/Header.astro';
import { familyTitle } from '@/lib/aspects';
import type { FamilyConfig, VotesConfig } from '@/lib/family';
import { defectTerms } from '@/lib/glossary';
import { defects, reportSchema } from '@/lib/report';
import { boardPath, familyPaths, systemPath, systemPaths, type Family } from '@/lib/reports';
import FamilyPage from '@/pages/[aspect]/[board]/index.astro';
import SystemPage from '@/pages/[aspect]/[board]/[system].astro';
import HomePage from '@/pages/index.astro';
import MethodologyPage from '@/pages/methodology.astro';
import { FIXTURE_NAMES, readFixture, shownSlugs } from '@/test/fixture';

/** The families pages list, since the content collections stay empty under Vitest. */
const listed = vi.hoisted(() => ({ families: [] as Family[] }));
vi.mock(import('@/lib/reports'), async (importOriginal) => ({
  ...(await importOriginal()),
  getFamilies: () => Promise.resolve(listed.families),
}));

/** The fixture's systems and the family itself, whose table and directory names pages hide. */
const SLUGS = { ...FIXTURE_NAMES, 'writing-arena': 'Arena Writing' };

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

/** The fixture's round, after an earlier one in which every system scored 900. */
async function family(): Promise<Family> {
  const latest = reportSchema.parse(await readFixture());
  const earlier = structuredClone(latest);
  earlier.round = '2026-10-07';
  earlier.closed_at = '2026-10-07T18:00:00Z';
  if (earlier.results.scorer !== 'votes') {
    throw new Error('the fixture is a votes report');
  }
  for (const standing of earlier.results.standings) {
    standing.score = 900;
  }
  return listedFamily('writing-arena', arenaConfig, [earlier, latest]);
}

/** A family as `listFamilies` lists it under the writing aspect. */
function listedFamily(name: string, config: FamilyConfig, rounds: Family['rounds']): Family {
  return {
    name,
    title: familyTitle(config),
    aspect: 'writing',
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
    .replaceAll('&#x27;', "'")
    .replace(/\s+/g, ' ');

/** A family scored by tests, with one round that lists the weaker system first. */
function testsFamily(): Family {
  const report = reportSchema.parse({
    family: 'writing-smoke',
    round: '2026-10-21',
    closed_at: '2026-10-21T18:00:00Z',
    results: {
      scorer: 'tests',
      standings: [
        {
          system: 'acme-small',
          fingerprint: null,
          trials: 4,
          errored: 0,
          pass_rate: 0.25,
          mean_cost_usd: null,
          p50_latency_sec: null,
        },
        {
          system: 'acme-pro',
          fingerprint: null,
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

/** The methodology section a family page's "How to read this board" link names. */
function guide(html: string): string {
  const match = /href="\/methodology\/#([\w-]+)"/.exec(html);
  if (!match) {
    throw new Error('the family page links no methodology section');
  }
  return match[1];
}

/** Every root-relative page link, leaving out the home page, its anchors, and the methodology. */
function boardLinks(html: string): string[] {
  return [...html.matchAll(/href="(\/[^"#]+)"/g)]
    .map(([, href]) => href)
    .filter((href) => !href.startsWith('/methodology/') && !/\.\w+$/.test(href));
}

/** The text of each scoring step's detail that opens a hint, in strip order. */
function stepHints(html: string): string[] {
  return [...html.matchAll(/<ol aria-label="\w+ scoring steps"[\s\S]*?<\/ol>/g)].flatMap(
    ([strip]) =>
      [...strip.matchAll(/<button[^>]*aria-haspopup="dialog"[^>]*>([^<]*)<\/button>/g)].map(
        ([, label]) => label,
      ),
  );
}

describe('family page', () => {
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
    expect(text(html)).toContain(
      'Leader Acme Large tied with the top score, chosen by the lower cost',
    );
    expect(text(html)).toContain('1–3 Acme Pro 1,004 955–1,065 114');
  });

  test('lists the systems with unbounded intervals before the first round closes', async () => {
    const empty: Family = { ...(await family()), rounds: [] };

    const html = await render(FamilyPage, { family: empty, boards: [] });

    expect(text(html)).toContain('No round has closed yet.');
    expect(text(html)).toContain('Acme Large Fixed at 1,000 as the anchor 1,000');
    expect(text(html)).toContain('Acme Pro Unbounded no votes');
    expect(text(html)).not.toMatch(/\bLeader\b/);
  });

  test('keys the empty board to the marks it draws', async () => {
    const empty: Family = { ...(await family()), rounds: [] };

    const html = await render(FamilyPage, { family: empty, boards: [] });

    expect(text(html)).toContain('Anchor, 1,000 Unbounded');
    expect(html).not.toContain('Score, 95% interval');
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

  test.each([
    ['with rounds', family],
    ['before its first round', async () => ({ ...(await family()), rounds: [] })],
    ['scored by tests', () => Promise.resolve(testsFamily())],
  ])('names the family and its systems by display name %s', async (_, load) => {
    const loaded = await load();

    const html = await render(FamilyPage, { family: loaded, boards: [] });

    expect(html).toContain(`<title>${loaded.title} | HAKULA†BENCH</title>`);
    expect(text(html)).toContain(`${loaded.title} Run `);
    expect(shownSlugs(html, { ...SLUGS, 'writing-smoke': 'Smoke Tasks' })).toEqual([]);
  });

  test.each([
    ['votes', family],
    ['tests', () => Promise.resolve(testsFamily())],
  ])("links the %s board to its scorer's methodology section", async (scorer, load) => {
    const methodology = await render(MethodologyPage, {});

    const html = await render(FamilyPage, { family: await load(), boards: [] });

    expect(guide(html)).toBe(scorer);
    expect(methodology).toContain(`id="${scorer}"`);
  });
});

describe('methodology page', () => {
  test("draws each scorer's steps without a family's hints", async () => {
    const html = await render(MethodologyPage, {});

    expect(text(html)).toContain('Run same tasks for all');
    expect(stepHints(html)).toEqual([]);
  });
});

describe('home page', () => {
  test("keys the shared legend to the marks the families' panels draw", async () => {
    listed.families = [{ ...(await family()), rounds: [] }, testsFamily()];

    const html = await render(HomePage, {});

    const legend = /<ul aria-label="Legend"[^>]*>([\s\S]*?)<\/ul>/.exec(html)?.[1] ?? '';
    expect(text(legend).trim()).toBe('Anchor, 1,000 Unbounded');
  });
});

describe('home page anchors', () => {
  test("titles each aspect's section with the anchor its breadcrumbs link", async () => {
    listed.families = [await family()];

    const html = await render(HomePage, {});

    expect(html).toMatch(/<h2 id="writing"[^>]*>[\s\S]*?Writing\s*<\/h2>/);
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

  test("draws the latest round's intervals and names its leader", async () => {
    const html = await render(FamilyPanel, { family: await family() });

    expect(text(html)).toContain('Votes · Round 2026-10-21, 340 votes');
    expect(text(html)).toContain('Acme Pro 955–1,065 1,004');
    expect(html).toContain('aria-label="Leader"');
    expect(text(html)).toContain('Acme Large, tied with the top score, chosen by the lower cost');
  });

  test('keeps the unranked systems and those without votes after the round', async () => {
    const html = await render(FamilyPanel, { family: await family() });

    expect(text(html)).toContain('Acme Edge Unbounded, too few comparisons to place it unranked');
    expect(text(html)).toContain('Acme New Unbounded no votes');
  });

  test.each([
    ['with rounds', family],
    ['before its first round', async () => ({ ...(await family()), rounds: [] })],
    ['scored by tests', () => Promise.resolve(testsFamily())],
  ])('names the family and its systems by display name %s', async (_, load) => {
    const loaded = await load();

    const html = await render(FamilyPanel, { family: loaded });

    expect(text(html)).toContain(`${loaded.config.display_name} `);
    expect(shownSlugs(html, { ...SLUGS, 'writing-smoke': 'Smoke Tasks' })).toEqual([]);
  });

  test("lists a tests family's systems by pass rate, best first", async () => {
    const html = await render(FamilyPanel, { family: testsFamily() });

    expect(text(html)).toContain('System Pass rate Acme Pro 75% Acme Small 25%');
  });
});

describe('system page', () => {
  test("summarizes the system's own standing in its latest round", async () => {
    const html = await render(SystemPage, {
      family: await family(),
      system: 'acme-pro',
      boards: [],
    });

    expect(text(html)).toContain(
      'Writing Arena Writing Acme Pro Score 1,004 (955–1,065) Rank 1–3 Votes 114 (89 decisive) Round 2026-10-21',
    );
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

  test('names which output length the rater favors', async () => {
    const html = await render(SystemPage, {
      family: await family(),
      system: 'acme-pro',
      boards: [],
    });

    expect(text(html)).toContain(
      "Length preference Shorter, 6.5 points per SD of the pair's relative length difference",
    );
  });

  test("counts each issue tagged on the system's outputs, with its share and meaning", async () => {
    const html = await render(SystemPage, {
      family: await family(),
      system: 'acme-mini',
      boards: [],
    });

    expect(text(html)).toContain(
      "Issues Votes that tagged this system's output with each issue. Issue Votes Share Wrong facts 3 2% Punctuation 4 3% Wrong tone 4 3% Formulaic 0 0% Too long 1 1%",
    );
    const table = [...new JSDOM(html).window.document.querySelectorAll('astro-island')].find(
      (island) => island.textContent.includes('Wrong facts'),
    );
    expect(
      [...(table?.querySelectorAll('button[aria-haspopup="dialog"]') ?? [])].map(
        (hint) => hint.textContent,
      ),
    ).toEqual(defects.map((defect) => defectTerms[defect].term));
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

    expect(text(html)).toContain('Round Score 95% interval Votes');
    expect(text(html)).toMatch(/2026-10-07 Anchor/);
    expect(text(html)).not.toMatch(/2026-10-21 Anchor/);
  });

  test('leaves the round unmarked when every round has one anchor', async () => {
    const html = await render(SystemPage, {
      family: await family(),
      system: 'acme-pro',
      boards: [],
    });

    expect(text(html)).toContain('Round Score 95% interval Votes');
    expect(text(html)).not.toContain('Anchor');
  });

  test('leaves out the head-to-head chart for a system without votes', async () => {
    const html = await render(SystemPage, {
      family: await family(),
      system: 'acme-tiny',
      boards: [],
    });

    expect(text(html)).toContain(
      'Acme Tiny Preliminary Score 233 (81–321) Rank 6 Votes 0 (0 decisive)',
    );
    expect(html).not.toContain('Head to head');
    expect(text(html)).toContain('Issue Votes Share Wrong facts 0 – Punctuation 0 –');
  });
});

const classesOf = (element: Element | null) => element?.getAttribute('class') ?? '';

/** The blocks of a stack as they lay out, looking through the `display: contents` islands. */
function blocks(stack: Element): Element[] {
  return [...stack.children].flatMap((child) =>
    child.localName === 'astro-island' ? [...child.children] : [child],
  );
}

describe('stacks', () => {
  test.each([
    ['family page', FamilyPage],
    ['system page', SystemPage],
  ])('space the islands on the %s with gap', async (_, page) => {
    const html = await render(page, { family: await family(), system: 'acme-pro', boards: [] });
    const parents = [...new JSDOM(html).window.document.querySelectorAll('astro-island')].map(
      (island) => classesOf(island.parentElement),
    );
    expect(parents).not.toEqual([]);

    expect(
      parents.filter((classes) => !/\bgap-/.test(classes) || /\bspace-[xy]-/.test(classes)),
    ).toEqual([]);
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

/** The props of the page at `params` among a route's static paths. */
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
      ]);
    }
  });

  test('marks the link to the page itself as the current page', async () => {
    const html = await render(Header, { section: 'methodology' }, '/methodology/');

    expect(navLinks(html, 'Main')).toEqual([
      ['/', 'Leaderboards', null],
      ['/methodology/', 'Methodology', 'page'],
    ]);
  });
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

describe('system page breadcrumb', () => {
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
