import { describe, expect, test } from 'vitest';

import { agreementTerm, anchorTerm, fallbackAnchor, preliminaryTerm } from '@/lib/glossary';
import { reportSchema } from '@/lib/report';
import { readFixture } from '@/test/fixture';

describe('recorded ranking terms', () => {
  test("uses each round's anchor and coverage rules in explanatory copy", async () => {
    const report = reportSchema.parse(await readFixture());
    if (report.results.scorer !== 'votes') {
      throw new Error('Expected votes fixture');
    }
    const method = { ...report.results.method, anchor_score: 1234 };
    const rules = {
      ...report.results.rules,
      preliminary_decisive_votes: 12,
      rater_agreement_floor: 0.5,
    };

    expect(anchorTerm(method).definition).toContain('1,234');
    expect(preliminaryTerm(rules).definition).toContain('12 raw votes');
    expect(agreementTerm(rules).definition).toContain('50%');
    expect(
      fallbackAnchor('Historical Anchor', 'Historical Reference', method).definition,
    ).toContain('Historical Anchor, fixed at 1,234 in place of the reference Historical Reference');
  });
});
