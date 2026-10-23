import { describe, expect, test } from 'vitest';

import { markdown, splitTail } from '@/lib/markdown';

describe('splitTail', () => {
  test.each([
    ['two words in English', 'Run the tests before you merge.', 'you merge.'],
    ['at least six characters in Chinese', '原因是磁盘写满，导致查询超时。', '导致查询超时。'],
    ['the whole text when it is short', '超时。', '超时。'],
  ])('keeps %s together', (_, text, tail) => {
    expect(splitTail(text)).toEqual([text.slice(0, text.length - tail.length), tail]);
  });

  test('keeps nothing together when the tail would overflow a narrow column', () => {
    const text = 'Then call validateEverythingBeforeMerging';

    expect(splitTail(text)).toEqual([text, '']);
  });
});

describe('markdown', () => {
  test('keeps the end of each list item on one line', () => {
    const html = markdown.render(
      '- 原因是磁盘写满，导致查询超时。\n- Run the tests before you merge.',
    );

    expect(html).toContain('原因是磁盘写满，<span class="whitespace-nowrap">导致查询超时。</span>');
    expect(html).toContain(
      'Run the tests before <span class="whitespace-nowrap">you merge.</span>',
    );
  });

  test('keeps the end of an inline line together without a paragraph around it', () => {
    expect(markdown.renderInline('Names `make test` and what it found')).toBe(
      'Names <code>make test</code> and what <span class="whitespace-nowrap">it found</span>',
    );
  });
});
