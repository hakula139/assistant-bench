// @vitest-environment jsdom
import { act, cleanup, render, renderHook, screen } from '@testing-library/react';
import { renderToString } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, test } from 'vitest';

import { RoundSwitcher } from '@/components/site/RoundSwitcher';
import { useSearchParam } from '@/lib/use-search-param';

const rounds = [
  { round: '2026-10-21', href: '/writing/zh/' },
  { round: '2026-10-07', href: '/writing/zh/rounds/2026-10-07/' },
];

const shownLinks = () =>
  screen.getAllByRole('link').map((link) => [link.textContent, link.getAttribute('href')]);

describe('RoundSwitcher', () => {
  beforeEach(() => {
    history.replaceState(null, '', '/writing/zh/rounds/2026-10-07/?category=message');
  });

  afterEach(cleanup);

  test('links each round without the query string in the static render', () => {
    const html = renderToString(<RoundSwitcher rounds={rounds} shown="2026-10-07" />);

    expect(html).toContain('href="/writing/zh/"');
    expect(html).toContain('href="/writing/zh/rounds/2026-10-07/"');
    expect(html).not.toContain('category');
  });

  test("carries the board's filters to every round as they change", () => {
    render(<RoundSwitcher rounds={rounds} shown="2026-10-07" />);
    const { result } = renderHook(() => useSearchParam('category', null));
    const loaded = shownLinks();

    act(() => {
      result.current[1]('docs');
    });

    expect(loaded).toEqual([
      ['2026-10-21 (latest)', '/writing/zh/?category=message'],
      ['2026-10-07', '/writing/zh/rounds/2026-10-07/?category=message'],
    ]);
    expect(shownLinks()).toEqual([
      ['2026-10-21 (latest)', '/writing/zh/?category=docs'],
      ['2026-10-07', '/writing/zh/rounds/2026-10-07/?category=docs'],
    ]);
  });
});
