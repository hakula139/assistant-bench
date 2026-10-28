import { describe, expect, test } from 'vitest';

import { actorCapTerm, anchorTerm, fallbackAnchor, preliminaryTerm } from '@/lib/glossary';
import { reportSchema } from '@/lib/report';
import { readFixture } from '@/test/fixture';

describe('recorded ranking terms', () => {
  test("uses each round's anchor and coverage rules in explanatory copy", async () => {
    const report = reportSchema.parse(await readFixture());
    if (report.results.scorer !== 'votes') {
      throw new Error('Expected votes fixture');
    }
    const method = { ...report.results.method, anchor_score: 1234, actor_cap: 25 };
    const rules = { ...report.results.rules, preliminary_decisive_votes: 12 };

    expect(anchorTerm(method).definition).toContain('1,234');
    expect(actorCapTerm(method).definition).toContain('more than 25 votes');
    expect(preliminaryTerm(rules).definition).toContain('12 raw votes');
    expect(
      fallbackAnchor('Historical Anchor', 'Historical Reference', method).definition,
    ).toContain('Historical Anchor, fixed at 1,234 in place of the reference Historical Reference');
  });
});
