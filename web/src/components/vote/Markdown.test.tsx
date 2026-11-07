// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, test } from 'vitest';

import { Markdown } from '@/components/vote/Markdown';

describe('Markdown', () => {
  afterEach(cleanup);

  test.each([
    ['unchecked', ' ', false],
    ['checked', 'x', true],
    ['uppercase', 'X', true],
  ])('renders %s tasks as named checkboxes that cannot be edited', async (_, marker, checked) => {
    const user = userEvent.setup();
    render(<Markdown text={`- [${marker}] Complete this task.`} />);
    const checkbox = screen.getByRole<HTMLInputElement>('checkbox', {
      name: 'Complete this task.',
    });

    expect(checkbox.disabled).toBe(true);
    expect(checkbox.checked).toBe(checked);
    await user.click(checkbox);
    await user.click(screen.getByText('Complete', { exact: false }));

    expect(checkbox.checked).toBe(checked);
    expect(checkbox.labels?.[0].textContent.trim()).toBe('Complete this task.');
  });

  test('preserves nested task labels and ordinary list items independently', () => {
    render(
      <Markdown
        text={
          '- [X] Review **the plan**.\n  - [ ] Run `make test`.\n  - Ordinary child.\n- Ordinary top.'
        }
      />,
    );

    const parent = screen.getByRole<HTMLInputElement>('checkbox', { name: 'Review the plan.' });
    const child = screen.getByRole<HTMLInputElement>('checkbox', { name: 'Run make test.' });
    expect(parent.checked).toBe(true);
    expect(child.checked).toBe(false);
    expect(child.closest('li')?.parentElement?.parentElement).toBe(parent.closest('li'));
    expect(parent.labels?.[0].contains(child)).toBe(false);
    for (const name of ['Ordinary child.', 'Ordinary top.']) {
      expect(screen.getByText(name).closest('li')?.querySelector('input, label')).toBeNull();
    }
  });

  test.each([
    ['a fenced block', '```text\n- [x] Keep this literal.\n```', '- [x] Keep this literal.\n'],
    ['inline code', '`- [ ] Keep this literal.`', '- [ ] Keep this literal.'],
  ])('does not turn task markers in %s into controls', (_, text, literal) => {
    const { container } = render(<Markdown text={text} />);

    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(container.querySelector('code')?.textContent).toBe(literal);
  });

  test('keeps untrusted HTML and images inert inside a task label', () => {
    const html = '<input type="checkbox" checked>';
    const image = '![logo](https://example.com/logo.png)';

    const { container } = render(<Markdown text={`- [ ] ${html} ${image}`} />);

    expect(screen.getAllByRole('checkbox')).toHaveLength(1);
    expect(screen.getByRole<HTMLInputElement>('checkbox').disabled).toBe(true);
    expect(container.querySelector('img')).toBeNull();
    expect(container.textContent).toContain(html);
    expect(container.textContent).toContain('!logo');
  });

  test.each([
    ['a code span holding a backtick', 'Type `` `x` `` here.', ['`x`'], 'Type `x` here.'],
    [
      'code spans beside Chinese text',
      '运行`make test`后，再看`结果`。',
      ['make test', '结果'],
      '运行make test后，再看结果。',
    ],
  ])('renders %s as inline code without its backticks', (_, text, spans, shown) => {
    const { container } = render(<Markdown text={text} />);

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
    expect(container.querySelector('.not-prose')).toBeNull();
  });

  test('keeps long code lines out of the no-wrap tails so they can scroll or wrap', () => {
    const path = 'https://example.com/a/very/long/path/that/runs/past/a/phone/screen';
    const line = `curl --fail --silent ${path} | jq '.items[] | select(.ok)'`;

    const { container } = render(
      <Markdown text={`\`\`\`sh\n${line}\n\`\`\`\n\nThen open \`${path}\``} />,
    );

    expect(container.querySelector('pre > code')?.textContent).toBe(`${line}\n`);
    const span = container.querySelector('p > code');
    expect(span?.textContent).toBe(path);
    expect(span?.classList.contains('wrap-anywhere')).toBe(true);
    expect(container.querySelector('.text-nowrap')).toBeNull();
  });
});
