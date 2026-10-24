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

  test.each(['report', 'report-early'] as const)(
    'parses the %s fixture the pydantic models write',
    async (name) => {
      const report = await readFixture(name);

      const result = reportSchema.safeParse(report);

      expect(result.error?.issues ?? []).toEqual([]);
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
