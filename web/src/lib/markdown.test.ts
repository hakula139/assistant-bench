import { describe, expect, test } from 'vitest';

import { markdown } from '@/lib/markdown';

describe('markdown', () => {
  test('keeps the end of each list item on one line', () => {
    const html = markdown.render(
      '- 原因是磁盘写满，导致查询超时。\n- Run the tests before you merge.',
    );

    expect(html).toContain('原因是磁盘写满，<span class="text-nowrap">导致查询超时。</span>');
    expect(html).toContain('Run the tests before <span class="text-nowrap">you merge.</span>');
  });

  test.each([
    [
      'a code span',
      '在 2.0 里改为 `wrap = "preserve"`。',
      '在 2.0 里改为 <span class="text-nowrap"><code>wrap = &quot;preserve&quot;</code>。</span>',
    ],
    [
      'emphasis',
      'Then we **merge**.',
      'Then <span class="text-nowrap">we <strong>merge</strong>.</span>',
    ],
    ['a soft break', 'aaa bbb\nccc', 'aaa <span class="text-nowrap">bbb\nccc</span>'],
  ])('keeps a block that ends in %s together with the words before it', (_, text, html) => {
    expect(markdown.render(text)).toBe(`<p>${html}</p>\n`);
  });

  test('keeps the end of an inline line together without a paragraph around it', () => {
    expect(markdown.renderInline('Names `make test` and what it found')).toBe(
      'Names <code>make test</code> and what <span class="text-nowrap">it found</span>',
    );
  });
});
