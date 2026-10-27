// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, test } from 'vitest';

import { InlineMarkdown, Markdown } from '@/components/vote/Markdown';

describe('Markdown', () => {
  afterEach(cleanup);

  test.each([
    ['a code span', Markdown, 'Run `make test` first.', ['make test'], 'Run make test first.'],
    ['a code span holding a backtick', Markdown, 'Type `` `x` `` here.', ['`x`'], 'Type `x` here.'],
    [
      'code spans beside Chinese text',
      Markdown,
      '运行`make test`后，再看`结果`。',
      ['make test', '结果'],
      '运行make test后，再看结果。',
    ],
    [
      'a code span in a checklist item',
      InlineMarkdown,
      'Names `make test` and **why**',
      ['make test'],
      'Names make test and why',
    ],
  ])('renders %s as inline code without its backticks', (_, Component, text, spans, shown) => {
    const { container } = render(<Component text={text} />);

    const codes = [...container.querySelectorAll('code')];
    expect(codes.map((code) => code.textContent)).toEqual(spans);
    expect(codes.every((code) => code.classList.contains('not-prose'))).toBe(true);
    expect(container.querySelector('pre')).toBeNull();
    expect(container.textContent.trimEnd()).toBe(shown);
  });

  test.each([
    ['a fence with a language', '```python\nprint("hi")\n```\n', 'pre > code', 'language-python'],
    ['a fence without a language', '```\nplain text\n```\n', 'pre > code', ''],
    ['an indented block', 'Before:\n\n    make test\n    make lint\n', 'pre > code', ''],
    [
      'a fence in a list item',
      '1. Run:\n\n   ```bash\n   ls -la\n   ```\n',
      'li > pre > code',
      'language-bash',
    ],
  ])('renders %s as a code block', (_, text, selector, language) => {
    const { container } = render(<Markdown text={text} />);

    const blocks = container.querySelectorAll('code');
    expect(blocks).toHaveLength(1);
    expect(blocks[0].matches(selector)).toBe(true);
    expect(blocks[0].className).toBe(language);
    expect(blocks[0].closest('p')).toBeNull();
  });

  test('keeps long code lines out of the no-wrap tails so they can scroll or wrap', () => {
    const path = 'https://example.com/a/very/long/path/that/runs/past/a/phone/screen';
    const line = `curl --fail --silent ${path} | jq '.items[] | select(.ok)'`;

    const { container } = render(
      <Markdown text={`\`\`\`sh\n${line}\n\`\`\`\n\nThen open \`${path}\``} />,
    );

    expect(container.querySelector('pre > code')?.textContent).toBe(`${line}\n`);
    expect(container.querySelector('p > code')?.textContent).toBe(path);
    expect(container.querySelector('.text-nowrap')).toBeNull();
  });
});
