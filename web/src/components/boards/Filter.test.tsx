// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';

import { Filter, RoundFilter } from '@/components/boards/Filter';

describe('Filter', () => {
  afterEach(cleanup);

  test('labels its select and reports the chosen option', () => {
    const onChange = vi.fn();
    render(
      <Filter
        label="Category"
        value="all"
        options={[
          { value: 'all', label: 'All categories' },
          { value: 'message', label: 'message' },
        ]}
        onChange={onChange}
      />,
    );

    fireEvent.change(screen.getByLabelText('Category'), { target: { value: 'message' } });

    expect(onChange.mock.calls).toEqual([['message']]);
  });
});

describe('RoundFilter', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  const rounds = [
    { round: '2026-10-21', href: '/writing/zh/' },
    { round: '2026-10-14', href: '/writing/zh/rounds/2026-10-14/' },
    { round: '2026-10-07', href: '/writing/zh/rounds/2026-10-07/' },
  ];

  test('marks the newest round as the latest and selects the one shown', () => {
    render(<RoundFilter rounds={rounds} current="2026-10-14" />);

    const select = screen.getByLabelText<HTMLSelectElement>('Round');
    expect([...select.options].map((option) => [option.text, option.selected])).toEqual([
      ['2026-10-21 (latest)', false],
      ['2026-10-14', true],
      ['2026-10-07', false],
    ]);
  });

  test.each([
    ['an earlier round', '2026-10-07', '/writing/zh/rounds/2026-10-07/?category=message'],
    ['the latest round', '2026-10-21', '/writing/zh/?category=message'],
  ])("opens %s's page and keeps the other filters", (_, round, href) => {
    const assign = vi.fn();
    vi.stubGlobal('location', { search: '?category=message', assign });
    render(<RoundFilter rounds={rounds} current="2026-10-14" />);

    fireEvent.change(screen.getByLabelText('Round'), { target: { value: round } });

    expect(assign.mock.calls).toEqual([[href]]);
  });
});
