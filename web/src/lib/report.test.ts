import { describe, expect, test } from 'vitest';

import { reportSchema } from '@/lib/report';
import { readFixture } from '@/test/fixture';

describe('reportSchema', () => {
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
