import * as Plot from '@observablehq/plot';
import { useCallback, useMemo } from 'react';

import { PlotFigure, type PlotFrame } from '@/components/charts/PlotFigure';
import { forestMarks, type ForestDatum } from '@/components/charts/forest-marks';
import { REFERENCE_SCORE, clip, forestDomain } from '@/lib/forest';
import { formatCountOf, formatInterval, formatScore } from '@/lib/format';
import type { CategoryBoard } from '@/lib/report';

interface CategoryForestsProps {
  categories: CategoryBoard[];
  /** Systems from top to bottom, in the leaderboard's order. */
  systems: string[];
}

type Refit = CategoryBoard['standings'][number];

const ROW_HEIGHT = 30;
/** Room between a row label and the plot, in CSS pixels. */
const LABEL_GAP = 12;

function CategoryForest({
  board,
  systems,
  domain,
}: {
  board: CategoryBoard;
  systems: string[];
  domain: [number, number];
}) {
  const height = systems.length * ROW_HEIGHT + 36;
  const plot = useCallback(
    ({ measure }: PlotFrame): Plot.PlotOptions => {
      const data = board.standings.map((refit): ForestDatum & { refit: Refit } => ({
        y: refit.system,
        clipped: clip(refit, domain),
        preliminary: refit.preliminary,
        refit,
      }));
      return {
        height,
        marginLeft: Math.max(...systems.map(measure)) + LABEL_GAP,
        marginRight: 16,
        marginBottom: 28,
        x: { domain, label: null, grid: true, ticks: 4, tickFormat: formatScore },
        y: { domain: systems, label: null, tickSize: 0 },
        color: { type: 'identity' },
        marks: [
          Plot.ruleX([REFERENCE_SCORE], { stroke: 'var(--faint)', strokeOpacity: 0.6 }),
          forestMarks(data, domain),
          Plot.tip(
            data,
            Plot.pointerY({
              x: (datum: (typeof data)[number]) => datum.clipped.score,
              y: 'y',
              title: ({ refit }: (typeof data)[number]) =>
                `${refit.system}\n${formatScore(refit.score)} (${formatInterval(refit.interval)})\n${formatCountOf(refit.votes, 'vote')}${refit.preliminary ? ', preliminary' : ''}`,
              fontFamily: 'inherit',
            }),
          ),
        ],
      };
    },
    [board, systems, domain, height],
  );
  return (
    <figure className="space-y-1">
      <figcaption className="text-sm">
        <span className="font-medium">{board.category}</span>
        <span className="text-muted-foreground">, {formatCountOf(board.votes, 'vote')}</span>
      </figcaption>
      <PlotFigure plot={plot} height={height} />
    </figure>
  );
}

/** One forest plot per category, on an axis every category shares. */
export function CategoryForests({ categories, systems }: CategoryForestsProps) {
  const domain = useMemo(
    () => forestDomain(categories.flatMap((board) => board.standings)),
    [categories],
  );
  return (
    <div className="grid gap-x-8 gap-y-6 sm:grid-cols-2 lg:grid-cols-3">
      {categories.map((board) => (
        <CategoryForest key={board.category} board={board} systems={systems} domain={domain} />
      ))}
    </div>
  );
}
