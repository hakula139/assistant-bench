import { describe, expect, test } from 'vitest';

import { familyConfigSchema } from '@/lib/family';

const config = { scorer: 'tests', display_name: 'English', public: true };

describe('familyConfigSchema', () => {
  test('rejects an aspect the site does not know', () => {
    const result = familyConfigSchema.safeParse({ ...config, aspect: 'cooking' });

    expect(result.error?.issues.map((issue) => issue.path)).toEqual([['aspect']]);
  });

  test('rejects a public family without an aspect', () => {
    const result = familyConfigSchema.safeParse(config);

    expect(result.error?.issues.map((issue) => issue.message)).toEqual([
      'a public family needs an aspect',
    ]);
  });

  test('accepts a family without an aspect that stays off the site', () => {
    const result = familyConfigSchema.safeParse({ ...config, public: false });

    expect(result.success).toBe(true);
  });
});
