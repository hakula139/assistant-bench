import { expect, test } from 'vitest';

import { familyConfigSchema } from '@/lib/family';

const votes = {
  scorer: 'votes',
  aspect: 'coding',
  display_name: 'Repository Tasks',
  reference: 'acme-large',
  public: true,
};

test.each([
  ['an aspect', 'aspect', { ...votes, aspect: 'cooking' }],
  ['a render', 'render', { ...votes, render: 'pdf' }],
  ['a harness', 'harness', { ...votes, render: 'diff', harness: 'claude-code' }],
])('rejects %s the site does not know', (_, key, config) => {
  const result = familyConfigSchema.safeParse(config);

  expect(result.error?.issues.map((issue) => issue.path)).toEqual([[key]]);
});
