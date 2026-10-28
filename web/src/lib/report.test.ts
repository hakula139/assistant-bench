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

describe('reportSchema', () => {
  test.each(['unknown', 'punctuation'])(
    'rejects the %s issue code in an aggregate',
    async (defect) => {
      const payload = await readFixture();
      replaceField(payload, ['results', 'standings', 0, 'defects'], { [defect]: 1 });

      expect(reportSchema.safeParse(payload).success).toBe(false);
    },
  );

  test('roundtrips every writing issue count in the published order', async () => {
    const payload = await readFixture();
    const parsed = reportSchema.parse(JSON.parse(JSON.stringify(payload)));
    if (parsed.results.scorer !== 'votes') {
      throw new Error('Expected votes fixture');
    }
    const row = parsed.results.standings.find((standing) => standing.system === 'acme-mini');

    expect(row?.defects).toEqual({
      scope: 2,
      facts: 2,
      clarity: 2,
      tone: 2,
      mechanics: 0,
      formulaic: 2,
      length: 1,
    });
    expect(Object.keys(row?.defects ?? {})).toEqual([
      'scope',
      'facts',
      'clarity',
      'tone',
      'mechanics',
      'formulaic',
      'length',
    ]);
    expect(reportSchema.parse(parsed)).toEqual(parsed);
  });

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

  test.each(['report', 'report-early', 'report-ledger'] as const)(
    'parses the %s votes round the pydantic models write',
    async (name) => {
      const payload = await readFixture(name);

      const parsed = reportSchema.parse(payload);

      expect(parsed).toEqual(payload);
    },
  );

  test.each([
    ['standing', ['standings', 0]],
    ['head-to-head', ['head_to_head', 0]],
    ['category', ['categories', 0]],
    ['category standing', ['categories', 0, 'standings', 0]],
  ] satisfies [string, (string | number)[]][])(
    'rejects a negative %s effective vote total',
    async (_, path) => {
      const payload = await readFixture();
      replaceField(payload, ['results', ...path, 'effective_votes'], -1);

      expect(reportSchema.safeParse(payload).success).toBe(false);
    },
  );

  test.each([
    ['missing provenance', ['provenance'], undefined],
    ['unrecorded provenance', ['provenance'], { status: 'unrecorded' }],
    ['negative cutoff', ['provenance', 'receipt_cutoff'], -1],
    ['private actor', ['provenance', 'actor_id'], 'private-actor'],
    ['nonpositive cap', ['method', 'actor_cap'], 0],
    ['boolean cap', ['method', 'actor_cap'], true],
    ['retired method name', ['method', 'name'], 'bradley-terry'],
  ] satisfies [string, (string | number)[], unknown][])(
    'rejects votes rounds with %s',
    async (_, path, value) => {
      const payload = await readFixture();
      replaceField(payload, ['results', ...path], value);

      expect(reportSchema.safeParse(payload).success).toBe(false);
    },
  );

  test('parses the tests round the pydantic models write', async () => {
    const payload = await readFixture('report-tests');

    const parsed = reportSchema.parse(payload);

    expect(parsed).toEqual(payload);
  });

  test.each([
    ['no standings', ['standings'], []],
    ['an interval above one', ['standings', 0, 'pass_rate', 'upper'], 1.5],
    ['pass^k with one sample', ['samples'], 1],
    ['pass^k missing with several samples', ['standings', 0, 'pass_hat_k'], null],
    ['a leader outside the standings', ['decision', 'leader'], 'acme-new'],
    ['a leader tied with itself', ['decision', 'tied'], ['acme-pro']],
    ['a repeated tie', ['decision', 'tied'], ['acme-mini', 'acme-mini']],
    ['an unknown category system', ['categories', 0, 'standings', 0, 'system'], 'acme-new'],
    ['a repeated category', ['categories', 1, 'category'], 'bug-fix'],
  ] satisfies [string, (string | number)[], unknown][])(
    'rejects tests results with %s',
    async (_, path, value) => {
      const payload = await readFixture('report-tests');
      replaceField(payload, ['results', ...path], value);

      expect(reportSchema.safeParse(payload).success).toBe(false);
    },
  );

  test.each([
    ['interval', ['standings', 0, 'interval'], { lower: 900, upper: 1100 }],
    ['standing fingerprint', ['standings', 0, 'fingerprint'], 'duplicate'],
    ['preliminary flag', ['standings', 0, 'preliminary'], true],
    ['category preliminary flag', ['categories', 0, 'standings', 0, 'preliminary'], true],
    ['unknown reference', ['reference'], 'unknown'],
    ['unknown anchor', ['anchor'], 'unknown'],
    ['unranked anchor', ['anchor'], 'acme-edge'],
    ['duplicate membership', ['unranked'], ['acme-large']],
    ['category duplicate membership', ['categories', 0, 'unranked'], ['acme-large']],
    ['unknown opponent', ['head_to_head', 0, 'opponent'], 'unknown'],
    ['unknown category', ['categories', 0, 'category'], 'unknown'],
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
      actor_cap: 50,
    });
    Object.assign(payload.results.rules, { preliminary_decisive_votes: 12 });

    const parsed = reportSchema.parse(payload);

    expect(parsed).toEqual(payload);
  });

  test('admits what the JSON Schema of the pydantic models admits', async () => {
    const committed = await readFixture('report.schema');

    const shape = schemaShape(z.toJSONSchema(reportSchema, { io: 'output' }));

    expect(shape).toEqual(schemaShape(committed));
  });

  test('preserves genuine capped ledger weights and provenance on the wire', async () => {
    const parsed = reportSchema.parse(await readFixture('report-ledger'));

    if (parsed.results.scorer !== 'votes') {
      throw new Error('Expected genuine votes fixture');
    }
    const results = parsed.results;
    expect(results.method.actor_cap).toBe(100);
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
