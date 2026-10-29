// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, test } from 'vitest';

import { Forest, ForestLegend } from '@/components/site/Forest';
import { forestDomain, forestTicks, roundRows } from '@/lib/forest';
import { formatScore } from '@/lib/format';
import { reportSchema } from '@/lib/report';
import { reportNames } from '@/lib/systems';
import { readFixture } from '@/test/fixture';
import { trackDrawing } from '@/test/track';

const estimate = (score: number) => ({ score });

describe('Forest', () => {
  afterEach(cleanup);

  test('draws settled and preliminary dots, an arrowhead below the axis, the anchor rule, and a gridline under each axis label', () => {
    const domain: [number, number] = [400, 1100];
    const grid = ['0%', '14.29%', '28.57%', '42.86%', '57.14%', '71.43%', '85.71%', '100%'];

    const { container } = render(
      <Forest
        caption="Example"
        domain={domain}
        anchorScore={1000}
        rows={[
          { system: 'settled', name: 'settled', estimate: estimate(1000), preliminary: false },
          {
            system: 'preliminary',
            name: 'preliminary',
            estimate: estimate(900),
            preliminary: true,
          },
          { system: 'cut', name: 'cut', estimate: estimate(233), preliminary: false },
          { system: 'new', name: 'new', unplaced: 'no-votes' },
        ]}
      />,
    );

    const drawings = [...container.querySelectorAll('tbody tr')].map(trackDrawing);
    const labels = [...container.querySelectorAll<HTMLElement>('thead [aria-hidden] span')];

    expect(drawings).toEqual([
      { dot: 'filled', arrows: [], dotAt: '85.71%', ruleAt: '85.71%', gridAt: grid },
      { dot: 'hollow', arrows: [], dotAt: '71.43%', ruleAt: '85.71%', gridAt: grid },
      { dot: 'none', arrows: ['start'], dotAt: null, ruleAt: '85.71%', gridAt: grid },
      { dot: 'none', arrows: [], dotAt: null, ruleAt: '85.71%', gridAt: grid },
    ]);
    expect(labels.map((label) => [label.textContent, label.style.left])).toEqual(
      forestTicks(domain).map((tick, index) => [formatScore(tick), grid[index]]),
    );
  });

  test('labels near-tied scores at their displayed precision', async () => {
    const report = reportSchema.parse(await readFixture());
    if (report.results.scorer !== 'votes') {
      throw new Error('the fixture is a votes report');
    }
    report.results.standings.forEach((standing, index) => {
      standing.score = 1000 + index / 100;
    });
    const rows = roundRows(
      () => '/',
      report.results,
      reportNames(report.systems),
      report.results.rules,
    );

    const { container } = render(
      <Forest
        caption="Near-tied scores"
        domain={forestDomain(report.results.standings)}
        rows={rows}
      />,
    );

    const labels = [...container.querySelectorAll('thead [aria-hidden] span')].map(
      (label) => label.textContent,
    );
    expect(new Set(labels).size).toBe(labels.length);
    expect(container.querySelector('tbody')?.textContent).toContain('Acme Large');
    expect(container.querySelector('tbody')?.textContent).toContain('Acme Small');
  });

  test.each<[[number, number], string, string[]]>([
    [[600, 1400], '@max-[10rem]:hidden', ['800', '1,200']],
    [[400, 1100], '@max-[17.5rem]:hidden', ['500', '600', '700', '800', '900', '1,000']],
    [[900, 1900], '@max-[25rem]:hidden', ['1,000', '1,200', '1,400', '1,600', '1,800']],
  ])(
    'hides the dense labels of %j below 2.5rem a narrowest interval with %s',
    (domain, hidden, dense) => {
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
    },
  );

  test('centers every legend mark in a key of one width', () => {
    const { container } = render(
      <ForestLegend marks={['score', 'preliminary', 'cut', 'anchor']} anchorScore={1000} />,
    );

    const keys = [...container.querySelectorAll('li > [aria-hidden]')];
    const marks = keys.map((key) => key.firstElementChild as HTMLElement);

    expect(keys.map((key) => key.className)).toEqual(keys.map(() => 'relative h-4 w-9 shrink-0'));
    expect(
      marks.map(
        (mark) => mark.style.left || [...mark.classList].find((name) => name === 'left-1/2'),
      ),
    ).toEqual(['50%', '50%', 'left-1/2', '50%']);
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
