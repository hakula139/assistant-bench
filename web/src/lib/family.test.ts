import { expect, test } from 'vitest';

import { familyConfigSchema } from '@/lib/family';

const votes = {
  scorer: 'votes',
  aspect: 'coding',
  display_name: 'Repository Tasks',
  categories: ['bug-fix', 'feature'],
  reference: 'acme-large',
  public: true,
};

test.each([
  ['an aspect', 'aspect', { ...votes, aspect: 'cooking' }],
  ['an aspect with spaces', 'aspect', { ...votes, aspect: 'private research' }],
  ['a private aspect named unlisted', 'aspect', { ...votes, aspect: 'unlisted' }],
  ['a render', 'render', { ...votes, render: 'pdf' }],
  ['a harness', 'harness', { ...votes, render: 'diff', harness: 'claude-code' }],
])('rejects %s the site does not know', (_, key, config) => {
  const result = familyConfigSchema.safeParse(config);

  expect(result.error?.issues.map((issue) => issue.path)).toEqual([[key]]);
});

test('rejects a category the site cannot label', () => {
  const result = familyConfigSchema.safeParse({ ...votes, categories: ['punctuation'] });

  expect(result.error?.issues.map((issue) => issue.path)).toEqual([['categories', 0]]);
});

test.each([
  ['private research', false],
  ['private research', undefined],
  ['unlisted', false],
  ['unlisted', undefined],
])('keeps private aspect %s when public is %s', (aspect, publicFlag) => {
  const parsed = familyConfigSchema.parse({ ...votes, aspect, public: publicFlag });

  expect(parsed.aspect).toBe(aspect);
  expect(parsed.public).toBe(false);
});
