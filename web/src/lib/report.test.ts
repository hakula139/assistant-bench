import { z } from 'astro/zod';
import { describe, expect, test } from 'vitest';

import { reportSchema } from '@/lib/report';
import { readFixture } from '@/test/fixture';
import { schemaShape } from '@/test/json-schema';

function replaceField(payload: object, path: (string | number)[], value: unknown): void {
  let parent = payload;
  for (const key of path.slice(0, -1)) {
    const child: unknown = Reflect.get(parent, key);
    if (typeof child !== 'object' || child === null) {
      throw new Error(`No object at ${path.join('.')}`);
    }
    parent = child;
  }
  Reflect.set(parent, path[path.length - 1], value);
}

async function cappedReport() {
  const payload = reportSchema.parse(await readFixture());
  if (payload.results.scorer !== 'votes') {
    throw new Error('Expected votes fixture');
  }

  const results = payload.results;
  results.method.human_weights = {
    name: 'actor-proportional-cap',
    version: 1,
    cap: 100,
    scope: 'family-evaluation',
    repeat_handling: 'excluded',
  };
  results.provenance = {
    status: 'ledger',
    schema_version: 1,
    export_id: 'export-1',
    export_digest: 'a'.repeat(64),
    namespace: 'test-deployment',
    captured_at: '2026-01-01T00:00:00Z',
    event_cutoff: 30,
    receipt_cutoff: 20,
    family_revision: 4,
    analysis_sequence: 30,
    evaluation_id: 'evaluation-1',
    publication_id: 'publication-1',
    policy_id: 'b'.repeat(64),
    sampling_point_snapshot_id: 'points-before',
    weighted_evidence_digest: 'c'.repeat(64),
    fitted_point_snapshot_id: 'points-after',
  };
  const aggregates = [
    results,
    ...results.standings,
    ...results.head_to_head,
    ...results.categories.flatMap((board) => [board, ...board.standings]),
  ];
  for (const aggregate of aggregates) {
    aggregate.effective_votes = aggregate.votes / 2;
  }

  return payload;
}

describe('reportSchema', () => {
  test.each([
    ['family', '../outside'],
    ['family', '/outside'],
    ['family', 'family\n'],
    ['round', '../outside'],
    ['round', '2026-10-21\n'],
    ['round', '２０２６-１０-２１'],
  ])('rejects unsafe frozen %s %j', async (field, value) => {
    const payload = await readFixture();

    expect(reportSchema.safeParse({ ...payload, [field]: value }).success).toBe(false);
  });

  test.each([
    'overall mass',
    'head total',
    'mirror mass',
    'mirror outcomes',
    'mirror rate',
    'category row',
    'category total',
    'duplicate category',
  ])('rejects capped cross-aggregate inconsistency in %s', async (change) => {
    const payload = await cappedReport();
    if (payload.results.scorer !== 'votes') {
      throw new Error('Expected votes fixture');
    }
    const results = payload.results;
    const first = results.head_to_head[0];
    const mirror = results.head_to_head.find(
      (row) => row.system === first.opponent && row.opponent === first.system,
    );
    if (mirror === undefined) {
      throw new Error('Expected mirrored fixture');
    }
    switch (change) {
      case 'overall mass':
        results.effective_votes = 1;
        break;
      case 'head total':
        results.votes += 1;
        break;
      case 'mirror mass':
        first.effective_votes += 0.25;
        mirror.effective_votes -= 0.25;
        break;
      case 'mirror outcomes':
        first.wins += 1;
        first.losses -= 1;
        break;
      case 'mirror rate':
        first.win_rate = 0.1;
        break;
      case 'category row':
        results.categories[0].effective_votes = 1;
        break;
      case 'category total':
        results.categories[0].effective_votes +=
          results.effective_votes -
          results.categories.reduce((sum, board) => sum + board.effective_votes, 0) +
          1;
        break;
      case 'duplicate category':
        results.categories[1].category = results.categories[0].category;
        break;
    }

    expect(reportSchema.safeParse(payload).success).toBe(false);
  });

  test('accepts capped fit mass with recorded ledger provenance', async () => {
    const payload = await cappedReport();

    const parsed = reportSchema.parse(payload);

    expect(parsed).toEqual(payload);
  });

  const massPaths: [string, (string | number)[]][] = [
    ['overall', []],
    ['standing', ['standings', 0]],
    ['head-to-head', ['head_to_head', 0]],
    ['category', ['categories', 0]],
    ['category standing', ['categories', 0, 'standings', 0]],
  ];
  test.each(
    massPaths.flatMap(([scope, path]) =>
      [-1, Infinity, NaN, 1000].map((value) => [scope, path, value] as const),
    ),
  )('rejects %s capped effective mass at %j: %s', async (_, path, value) => {
    const payload = await cappedReport();
    replaceField(payload, ['results', ...path, 'effective_votes'], value);

    expect(reportSchema.safeParse(payload).success).toBe(false);
  });

  test.each([
    ['missing provenance', ['provenance'], undefined],
    ['unrecorded provenance', ['provenance'], { status: 'unrecorded' }],
    ['negative cutoff', ['provenance', 'receipt_cutoff'], -1],
    ['private actor', ['provenance', 'actor_id'], 'private-actor'],
    ['unknown method', ['method', 'human_weights', 'name'], 'unknown-method'],
    ['unknown version', ['method', 'human_weights', 'version'], 2],
    ['nonpositive cap', ['method', 'human_weights', 'cap'], 0],
    ['boolean cap', ['method', 'human_weights', 'cap'], true],
    ['category cap', ['method', 'human_weights', 'scope'], 'category'],
    ['ranked repeats', ['method', 'human_weights', 'repeat_handling'], 'included'],
  ] satisfies [string, (string | number)[], unknown][])(
    'rejects capped reports with %s',
    async (_, path, value) => {
      const payload = await cappedReport();
      replaceField(payload, ['results', ...path], value);

      expect(reportSchema.safeParse(payload).success).toBe(false);
    },
  );

  test.each([
    ['interval', ['standings', 0, 'interval'], { lower: 900, upper: 1100 }],
    ['best rank', ['standings', 0, 'best_rank'], 1],
    ['worst rank', ['standings', 0, 'worst_rank'], 2],
    [
      'category interval',
      ['categories', 0, 'standings', 0, 'interval'],
      { lower: 900, upper: 1100 },
    ],
    ['separability', ['separability'], 0.5],
    ['decision', ['decision'], { leader: 'acme-large', separated: true, tie_break: 'score' }],
    ['standing fingerprint', ['standings', 0, 'fingerprint'], 'duplicate'],
    ['preliminary flag', ['standings', 0, 'preliminary'], true],
    ['category preliminary flag', ['categories', 0, 'standings', 0, 'preliminary'], true],
    ['overall mass', ['effective_votes'], 0.25],
    ['standing mass', ['standings', 0, 'effective_votes'], 0.25],
    ['category mass', ['categories', 0, 'effective_votes'], 0.25],
    ['category standing mass', ['categories', 0, 'standings', 0, 'effective_votes'], 0.25],
    ['head to head mass', ['head_to_head', 0, 'effective_votes'], 0.25],
    ['unknown reference', ['reference'], 'unknown'],
    ['unknown anchor', ['anchor'], 'unknown'],
    ['unranked anchor', ['anchor'], 'acme-edge'],
    ['duplicate membership', ['unranked'], ['acme-large']],
    ['category duplicate membership', ['categories', 0, 'unranked'], ['acme-large']],
    ['contradictory repeat floor', ['rater_agreement', 'below_floor'], true],
    ['unknown opponent', ['head_to_head', 0, 'opponent'], 'unknown'],
    ['unknown unranked system', ['unranked'], ['unknown']],
    ['unknown category system', ['categories', 0, 'unranked'], ['unknown']],
    ['empty system id', ['standings', 0, 'system'], ''],
  ] satisfies [string, (string | number)[], unknown][])(
    'rejects inconsistent %s',
    async (_, path, value) => {
      const payload = await readFixture();
      replaceField(payload, ['results', ...path], value);

      const result = reportSchema.safeParse(payload);

      expect(result.success).toBe(false);
    },
  );

  test.each(['missing', 'extra', 'blank label', 'duplicate label', 'extra field'])(
    'rejects %s public metadata',
    async (change) => {
      const payload = reportSchema.parse(await readFixture());
      const [first, second] = Object.keys(payload.systems);
      switch (change) {
        case 'missing':
          Reflect.deleteProperty(payload.systems, first);
          break;
        case 'extra':
          payload.systems.unused = { display_name: 'Unused', fingerprint: null };
          break;
        case 'blank label':
          payload.systems[first].display_name = '  ';
          break;
        case 'duplicate label':
          payload.systems[first].display_name = payload.systems[second].display_name;
          break;
        case 'extra field':
          Reflect.set(payload.systems[first], 'route', 'private-route');
          break;
      }

      expect(reportSchema.safeParse(payload).success).toBe(false);
    },
  );

  test('preserves historical method parameters and thresholds', async () => {
    const payload = reportSchema.parse(await readFixture());
    if (payload.results.scorer !== 'votes') {
      throw new Error('Expected votes fixture');
    }
    Object.assign(payload.results.method, {
      l2_penalty: 0.25,
      points_per_logit: 200,
      anchor_score: 1234,
    });
    Object.assign(payload.results.rules, {
      preliminary_decisive_votes: 12,
      rater_agreement_floor: 0.5,
    });

    const parsed = reportSchema.parse(payload);

    expect(parsed).toEqual(payload);
  });

  test('admits what the JSON Schema of the pydantic models admits', async () => {
    const committed = await readFixture('report.schema');

    const shape = schemaShape(z.toJSONSchema(reportSchema, { io: 'output' }));

    expect(shape).toEqual(schemaShape(committed));
  });

  test.each(['report', 'report-early', 'report-ledger'] as const)(
    'parses the %s fixture the pydantic models write',
    async (name) => {
      const report = await readFixture(name);

      const result = reportSchema.safeParse(report);

      expect(result.error?.issues ?? []).toEqual([]);
    },
  );

  test('preserves genuine capped ledger weights and provenance on the wire', async () => {
    const payload = await readFixture('report-ledger');

    const parsed = reportSchema.parse(payload);

    expect(parsed).toEqual(payload);
    if (parsed.results.scorer !== 'votes') {
      throw new Error('Expected genuine votes fixture');
    }
    const results = parsed.results;
    expect(results.method.human_weights.name).toBe('actor-proportional-cap');
    expect(results.provenance?.status).toBe('ledger');
    expect(results.votes).toBe(150);
    expect(results.dropped_votes).toBe(1);
    expect(results.effective_votes).toBeCloseTo(120);
    const large = results.head_to_head.find((row) => row.system === 'large');
    if (large === undefined) {
      throw new Error('Expected the large system comparison');
    }
    expect(large.win_rate).toBeCloseTo(7 / 12);
    expect(large.win_rate).not.toBeCloseTo(
      (large.wins + (large.ties + large.both_bad) / 2) / large.votes,
    );
  });

  test.each(['report', 'report-early'] as const)(
    'preserves omitted provenance in the historical %s wire object',
    async (name) => {
      const payload = await readFixture(name);

      const parsed = reportSchema.parse(payload);

      expect(parsed).toEqual(payload);
    },
  );

  test('rejects a field the pydantic models do not write', async () => {
    const report = await readFixture();
    const results = report.results as { standings: Record<string, unknown>[] };
    results.standings[0].model = 'acme/large';

    const result = reportSchema.safeParse(report);

    expect(result.error?.issues.map((issue) => issue.code)).toEqual(['unrecognized_keys']);
  });
});
