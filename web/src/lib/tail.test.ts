import { describe, expect, test } from 'vitest';

import { splitTail } from '@/lib/tail';

describe('splitTail', () => {
  test.each([
    ['two words in English', 'Run the tests before you merge.', 'you merge.'],
    ['at least six characters in Chinese', '原因是磁盘写满，导致查询超时。', '导致查询超时。'],
    ['the whole text when it is short', '超时。', '超时。'],
    [
      'nothing when the tail would overflow a narrow column',
      'Then call validateEverythingBeforeMerging',
      '',
    ],
  ])('keeps together %s', (_, text, tail) => {
    expect(splitTail(text)).toEqual([text.slice(0, text.length - tail.length), tail]);
  });
});
