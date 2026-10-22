import { renderToString } from 'react-dom/server';
import { describe, expect, test } from 'vitest';

import { Forest } from '@/components/site/Forest';

describe('Forest', () => {
  /** Each body row's markup, by system. */
  function rows(): Record<string, string> {
    const estimate = (score: number, lower: number, upper: number) => ({
      score,
      interval: { lower, upper },
    });
    const html = renderToString(
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
    const body = html.slice(html.indexOf('<tbody'));
    return Object.fromEntries(
      body
        .split('<tr')
        .slice(1)
        .map((row): [string, string] => [/scope="row"[^>]*>([^<]+)</.exec(row)?.[1] ?? '', row]),
    );
  }

  test('dashes only the preliminary interval', () => {
    const { settled, preliminary } = rows();

    expect(settled).toContain('border-solid');
    expect(settled).not.toContain('border-dashed');
    expect(preliminary).toContain('border-dashed');
  });

  test('ends an interval the axis cuts in an arrowhead', () => {
    const { settled, cut } = rows();

    expect(cut).toContain('<svg');
    expect(settled).not.toContain('<svg');
  });
});
