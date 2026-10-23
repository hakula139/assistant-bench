// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, test } from 'vitest';

import { Forest } from '@/components/site/Forest';

const estimate = (score: number, lower: number, upper: number) => ({
  score,
  interval: { lower, upper },
});

/** Names the look whose class an element carries, or `none` for a missing or unstyled element. */
function look(element: Element | null, looks: Record<string, string>): string {
  return Object.entries(looks).find(([style]) => element?.classList.contains(style))?.[1] ?? 'none';
}

/** How each body row draws its system, read off the track's styles. */
function drawings() {
  const { container } = render(
    <Forest
      caption="Example"
      domain={[400, 1100]}
      rows={[
        {
          system: 'settled',
          name: 'settled',
          estimate: estimate(1000, 950, 1050),
          preliminary: false,
        },
        {
          system: 'preliminary',
          name: 'preliminary',
          estimate: estimate(900, 800, 1000),
          preliminary: true,
        },
        { system: 'cut', name: 'cut', estimate: estimate(500, 300, 700), preliminary: false },
      ]}
    />,
  );
  return Object.fromEntries(
    [...container.querySelectorAll('tbody tr')].map((row) => [
      row.querySelector('th')?.textContent ?? '',
      {
        interval: look(row.querySelector('.border-t-3'), {
          'border-dashed': 'dashed',
          'border-solid': 'solid',
        }),
        dot: look(row.querySelector('.rounded-full'), {
          'bg-background': 'hollow',
          'bg-chart-series': 'filled',
        }),
        arrows: [...row.querySelectorAll('svg')].map((svg) =>
          look(svg, { 'left-0': 'start', 'right-0': 'end' }),
        ),
      },
    ]),
  );
}

describe('Forest', () => {
  afterEach(cleanup);

  test('draws a preliminary system as a hollow dot on a dashed interval', () => {
    const { settled, preliminary } = drawings();

    expect([settled, preliminary]).toEqual([
      { interval: 'solid', dot: 'filled', arrows: [] },
      { interval: 'dashed', dot: 'hollow', arrows: [] },
    ]);
  });

  test('ends an interval the axis cuts in an arrowhead at the axis start', () => {
    const { cut } = drawings();

    expect(cut.arrows).toEqual(['start']);
  });

  test.each<[[number, number], string, string[]]>([
    [[600, 1400], '@max-[10rem]:hidden', ['800', '1,200']],
    [[400, 1100], '@max-[17.5rem]:hidden', ['500', '600', '700', '800', '900', '1,000']],
  ])('hides the dense labels of %j below 2.5rem an interval with %s', (domain, hidden, dense) => {
    const { container } = render(<Forest caption="Example" domain={domain} rows={[]} />);

    const labels = [...container.querySelectorAll('thead [aria-hidden] span')];

    expect(
      labels.filter((label) => label.classList.contains(hidden)).map((l) => l.textContent),
    ).toEqual(dense);
    expect(
      labels.every(
        (label) => !label.className.includes('@max-') || label.classList.contains(hidden),
      ),
    ).toBe(true);
  });

  test("opens a row's score and interval from its score", async () => {
    render(
      <Forest
        caption="Example"
        domain={[400, 1100]}
        rows={[
          {
            system: 'pro',
            name: 'Acme Pro',
            estimate: estimate(1004, 955, 1065),
            preliminary: true,
          },
        ]}
      />,
    );

    await userEvent.setup().click(screen.getByRole('button', { name: '1,004' }));

    expect((await screen.findByRole('dialog', { name: 'Acme Pro' })).textContent).toContain(
      'Score 1,004, 95% interval 955–1,065, preliminary.',
    );
  });
});
