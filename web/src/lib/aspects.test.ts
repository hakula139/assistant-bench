import { expect, test } from 'vitest';

import { groupByAspect, type Aspect } from '@/lib/aspects';

test('groups items by aspect in their given order, with those without an aspect last', () => {
  const items: { name: string; aspect: Aspect | null }[] = [
    { name: 'smoke', aspect: null },
    { name: 'prose-zh', aspect: 'technical-writing' },
    { name: 'prose-en', aspect: 'technical-writing' },
  ];

  const groups = groupByAspect(items);

  expect(groups.map(({ aspect, members }) => [aspect, members.map((item) => item.name)])).toEqual([
    ['technical-writing', ['prose-zh', 'prose-en']],
    [null, ['smoke']],
  ]);
});
