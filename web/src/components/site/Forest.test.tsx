// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, test } from 'vitest';

import { Forest } from '@/components/site/Forest';
import { forestDomain, roundRows } from '@/lib/forest';
import { reportSchema } from '@/lib/report';
import { reportNames } from '@/lib/systems';
import { readFixture } from '@/test/fixture';
import { trackDrawing } from '@/test/track';

const estimate = (score: number) => ({ score });

describe('Forest', () => {
  afterEach(cleanup);

  test('draws settled and preliminary dots, an arrowhead below the axis, and the anchor rule', () => {
    const { container } = render(
      <Forest
        caption="Example"
        domain={[400, 1100]}
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

    expect(drawings).toEqual([
      { dot: 'filled', arrows: [], dotAt: '85.71%', ruleAt: '85.71%' },
      { dot: 'hollow', arrows: [], dotAt: '71.43%', ruleAt: '85.71%' },
      { dot: 'none', arrows: ['start'], dotAt: null, ruleAt: '85.71%' },
      { dot: 'none', arrows: [], dotAt: null, ruleAt: '85.71%' },
    ]);
  });

  test('labels the genuine near-tied ledger scores at their displayed precision', async () => {
    const report = reportSchema.parse(await readFixture('report-ledger'));
    if (report.results.scorer !== 'votes') {
      throw new Error('the fixture is a votes report');
    }
    const rows = roundRows(
      () => '/',
      report.results,
      reportNames(report.systems),
      report.results.rules,
    );

    const { container } = render(
      <Forest
        caption="Ledger scores"
        domain={forestDomain(report.results.standings)}
        rows={rows}
      />,
    );

    const labels = [...container.querySelectorAll('thead [aria-hidden] span')].map(
      (label) => label.textContent,
    );
    expect(new Set(labels).size).toBe(labels.length);
    expect(container.querySelector('tbody')?.textContent).toContain('Large');
    expect(container.querySelector('tbody')?.textContent).toContain('Small');
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
