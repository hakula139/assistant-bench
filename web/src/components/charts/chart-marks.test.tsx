// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { DotRangePlot } from '@/components/charts/DotRangePlot';
import { ForestPlot } from '@/components/charts/ForestPlot';
import { ParetoScatter } from '@/components/charts/ParetoScatter';
import { stubPlotFrame } from '@/test/plot';

const rules = {
  preliminary_decisive_votes: 40,
  preliminary_decisive_vote_unit: 'raw-votes' as const,
  rater_agreement_floor: 0.7,
};
const refit = (system: string, score: number) => ({
  system,
  score,
  interval: null,
  votes: 40,
  effective_votes: 40,
  decisive_votes: 40,
});

const charts: [string, ReactElement, number][] = [
  [
    'a forest plot',
    <ForestPlot
      key="forest"
      rows={[
        { label: 'docs', refit: refit('a', 1100) },
        { label: 'reply', refit: refit('a', 900) },
      ]}
      labels={['docs', 'reply']}
      domain={[600, 1200]}
      anchorScore={1000}
      rules={rules}
    />,
    1,
  ],
  [
    'a dot range plot',
    <DotRangePlot
      key="dots"
      rows={[
        { label: 'acme-pro', value: 0.6 },
        { label: 'acme-mini', value: 0.4 },
      ]}
      label="Win rate"
      format="percent"
      baseline={0.5}
    />,
    1,
  ],
  [
    'a scatter',
    <ParetoScatter
      key="scatter"
      points={[
        { system: 'a', name: 'A', x: 1, score: 900 },
        { system: 'b', name: 'B', x: 4, score: 1000 },
      ]}
      label="Price"
      format={(value) => `$${value}`}
    />,
    0,
  ],
];

describe('chart marks', () => {
  beforeEach(() => {
    stubPlotFrame();
  });

  afterEach(cleanup);

  test.each(charts)(
    'draws the shared gridlines, reference rules, and tips in %s',
    async (_, chart, references) => {
      const { container } = render(chart);
      const svg = await vi.waitFor(() => {
        const drawn = container.querySelector('svg');
        expect(drawn).not.toBeNull();
        return drawn as SVGSVGElement;
      });

      const grids = [...svg.querySelectorAll('g[aria-label$="-grid"]')];
      const referenceRules = [...svg.querySelectorAll('g[aria-label="rule"]')].filter(
        (rule) => rule.getAttribute('stroke') === 'var(--chart-reference)',
      );
      const tips = [...svg.querySelectorAll('g[aria-label="tip"]')];

      expect(grids).not.toEqual([]);
      expect(
        grids.map((grid) => [grid.getAttribute('stroke'), grid.getAttribute('stroke-opacity')]),
      ).toEqual(grids.map(() => ['var(--chart-grid)', null]));
      expect(referenceRules).toHaveLength(references);
      expect(tips).not.toEqual([]);
      expect(tips.map((tip) => tip.getAttribute('font-family'))).toEqual(tips.map(() => 'inherit'));
    },
  );
});
