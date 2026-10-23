// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';

import { OutputPane } from '@/components/vote/OutputPane';

describe('OutputPane', () => {
  afterEach(cleanup);

  test('loads an HTML output by URL in a frame that may only run scripts', () => {
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe() {}
        disconnect() {}
      },
    );
    const url = 'http://127.0.0.1:8791/render/t1/a';

    render(
      <OutputPane
        side="A"
        pane={{ render: 'html', url }}
        asMarkdown
        viewport={1280}
        controls={null}
      />,
    );

    const frame = screen.getByTitle('Output A');
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts');
    expect(frame.getAttribute('src')).toBe(url);
    expect(frame.hasAttribute('srcdoc')).toBe(false);
  });

  test('shows raw HTML and images in Markdown as text, without loading them', () => {
    const text = '<b>bold</b> ![logo](http://example.com/logo.png)';

    const { container } = render(
      <OutputPane
        side="B"
        pane={{ render: 'text', text }}
        asMarkdown
        viewport={1280}
        controls={null}
      />,
    );

    expect(container.querySelector('b, img')).toBeNull();
    expect(screen.getByRole('region', { name: 'Output B' }).textContent).toContain('<b>bold</b>');
  });

  test('shows a raw output as written, keeping the end of each line together', () => {
    const text = 'Spaced  out\n\n原因是磁盘写满，导致查询超时。';

    render(
      <OutputPane
        side="A"
        pane={{ render: 'text', text }}
        asMarkdown={false}
        viewport={1280}
        controls={null}
      />,
    );

    const pane = screen.getByRole('region', { name: 'Output A' });
    expect(pane.textContent).toBe(`Output A${text}`);
    expect([...pane.querySelectorAll('.text-nowrap')].map((tail) => tail.textContent)).toEqual([
      'Spaced  out',
      '导致查询超时。',
    ]);
  });
});
