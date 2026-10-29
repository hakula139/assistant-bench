// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, test } from 'vitest';

import { OutputPane } from '@/components/vote/OutputPane';

describe('OutputPane', () => {
  afterEach(cleanup);

  test('shows raw HTML and images in Markdown as text, without loading them', () => {
    const text = '<b>bold</b> ![logo](http://example.com/logo.png)';

    const { container } = render(
      <OutputPane side="B" render="text" text={text} asMarkdown controls={null} />,
    );

    expect(container.querySelector('b, img')).toBeNull();
    expect(screen.getByRole('region', { name: 'Output B' }).textContent).toContain('<b>bold</b>');
  });

  test('styles each line of a diff as a file header, hunk header, or change', () => {
    const text = [
      'diff --git a/q.sql b/q.sql',
      '--- a/q.sql',
      '+++ b/q.sql',
      '@@ -1,3 +1,3 @@',
      ' select 1;',
      '--- old comment',
      '+-- new comment',
      'diff --git a/logo.png b/logo.png',
      'Binary files a/logo.png and b/logo.png differ',
      '',
    ].join('\n');

    render(<OutputPane side="A" render="diff" text={text} asMarkdown controls={null} />);

    const lines = [...screen.getByRole('region').querySelectorAll('[data-diff]')];
    expect(lines.map((line) => [line.getAttribute('data-diff'), line.textContent])).toEqual([
      ['file', 'diff --git a/q.sql b/q.sql'],
      ['file', '--- a/q.sql'],
      ['file', '+++ b/q.sql'],
      ['hunk', '@@ -1,3 +1,3 @@'],
      ['context', ' select 1;'],
      ['removed', '--- old comment'],
      ['added', '+-- new comment'],
      ['file', 'diff --git a/logo.png b/logo.png'],
      ['file', 'Binary files a/logo.png and b/logo.png differ'],
    ]);
    expect(lines[5].className).toContain('bg-red-500/15');
    expect(lines[6].className).toContain('bg-emerald-500/15');
    expect(lines[7].hasAttribute('data-start')).toBe(true);
  });

  test('shows a raw output as written, keeping the end of each line together', () => {
    const text = 'Spaced  out\n\n原因是磁盘写满，导致查询超时。';

    render(<OutputPane side="A" render="text" text={text} asMarkdown={false} controls={null} />);

    const pane = screen.getByRole('region', { name: 'Output A' });
    expect(pane.textContent).toBe(`Output A${text}`);
    expect([...pane.querySelectorAll('.text-nowrap')].map((tail) => tail.textContent)).toEqual([
      'Spaced  out',
      '导致查询超时。',
    ]);
  });
});
