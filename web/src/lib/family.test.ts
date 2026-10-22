import { expect, test } from 'vitest';

import { familyConfigSchema } from '@/lib/family';

test('rejects an aspect the site does not know', () => {
  const config = { scorer: 'tests', aspect: 'cooking', display_name: 'English', public: true };

  const result = familyConfigSchema.safeParse(config);

  expect(result.error?.issues.map((issue) => issue.path)).toEqual([['aspect']]);
});
