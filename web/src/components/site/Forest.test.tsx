// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, test } from 'vitest';

import { Forest } from '@/components/site/Forest';

const estimate = (score: number) => ({ score });

function look(element: Element | null, looks: Record<string, string>): string {
  return Object.entries(looks).find(([style]) => element?.classList.contains(style))?.[1] ?? 'none';
}

function drawings() {
  const { container } = render(
    <Forest
      caption="Example"
      domain={[400, 1100]}
      rows={[
        {
          system: 'settled',
          name: 'settled',
          estimate: estimate(1000),
          preliminary: false,
        },
        {
          system: 'preliminary',
          name: 'preliminary',
          estimate: estimate(900),
          preliminary: true,
        },
        { system: 'cut', name: 'cut', estimate: estimate(233), preliminary: false },
      ]}
    />,
  );
  return Object.fromEntries(
    [...container.querySelectorAll('tbody tr')].map((row) => [
      row.querySelector('th')?.textContent ?? '',
      {
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

  test('draws preliminary coverage as a hollow dot', () => {
    const { settled, preliminary } = drawings();

    expect([settled, preliminary]).toEqual([
      { dot: 'filled', arrows: [] },
      { dot: 'hollow', arrows: [] },
    ]);
  });

  test('keeps a point below the axis visible as an arrowhead', () => {
    const { cut } = drawings();

    expect(cut.arrows).toEqual(['start']);
  });

  test.each<[[number, number], string, string[]]>([
    [[600, 1400], '@max-[10rem]:hidden', ['800', '1,200']],
    [[400, 1100], '@max-[17.5rem]:hidden', ['500', '600', '700', '800', '900', '1,000']],
  ])('hides the dense labels of %j below 2.5rem an interval with %s', (domain, hidden, dense) => {
    const { container } = render(
      <Forest caption="Example" domain={domain} rows={[]} anchorScore={1000} />,
    );

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

  test("opens a row's point score from its score", async () => {
    render(
      <Forest
        caption="Example"
        domain={[400, 1100]}
        rows={[
          {
            system: 'pro',
            name: 'Acme Pro',
            estimate: estimate(1004),
            preliminary: true,
          },
        ]}
      />,
    );

    await userEvent.setup().click(screen.getByRole('button', { name: '1,004' }));

    expect((await screen.findByRole('dialog', { name: 'Acme Pro' })).textContent).toContain(
      'Point score 1,004, preliminary.',
    );
  });
});
