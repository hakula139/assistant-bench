import { expect, test } from 'vitest';

import { groupByAspect, type Aspect } from '@/lib/aspects';

test('groups items by aspect in their given order, with those without an aspect last', () => {
  const items: { name: string; aspect: Aspect | null }[] = [
    { name: 'smoke', aspect: null },
    { name: 'writing-zh', aspect: 'writing' },
    { name: 'writing-en', aspect: 'writing' },
  ];

  const groups = groupByAspect(items);

  expect(groups.map(({ aspect, members }) => [aspect, members.map((item) => item.name)])).toEqual([
    ['writing', ['writing-zh', 'writing-en']],
    [null, ['smoke']],
  ]);
});
