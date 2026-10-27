// @vitest-environment jsdom
import * as Plot from '@observablehq/plot';
import { cleanup, render } from '@testing-library/react';
import axe from 'axe-core';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { PlotFigure } from '@/components/charts/PlotFigure';
import { stubPlotFrame } from '@/test/plot';

describe('PlotFigure', () => {
  beforeEach(() => {
    stubPlotFrame();
  });

  afterEach(cleanup);

  test("keeps Plot's per-mark labels out of the accessibility tree", async () => {
    const { container } = render(
      <PlotFigure
        label="Scores"
        height={120}
        plot={() => ({
          marks: [
            Plot.dot([{ score: 1 }, { score: 2 }], { x: 'score', tip: true }),
            Plot.ruleX([0]),
          ],
        })}
      />,
    );
    await vi.waitFor(() => {
      expect(container.querySelector('svg g[aria-label="dot"]')).not.toBeNull();
    });

    const { violations } = await axe.run(container);

    expect(violations.map(({ id, nodes }) => `${id}: ${String(nodes.length)}`)).toEqual([]);
  });
});
