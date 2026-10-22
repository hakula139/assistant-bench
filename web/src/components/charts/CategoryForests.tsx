import { useMemo } from 'react';

import { ForestPlot } from '@/components/charts/ForestPlot';
import { sharedDomain } from '@/lib/forest';
import { formatCountOf } from '@/lib/format';
import type { CategoryBoard } from '@/lib/report';
import { systemName, type SystemNames } from '@/lib/systems';

interface CategoryForestsProps {
  categories: CategoryBoard[];
  /** Systems from top to bottom, in the leaderboard's order. */
  systems: string[];
  names: SystemNames;
}

/** One forest plot per category, on an axis every category shares. */
export function CategoryForests({ categories, systems, names }: CategoryForestsProps) {
  const domain = useMemo(
    () => sharedDomain(categories.map((board) => board.standings)),
    [categories],
  );
  const labels = useMemo(
    () => systems.map((system) => systemName(names, system)),
    [systems, names],
  );
  return (
    <div className="grid gap-x-8 gap-y-6 sm:grid-cols-2 lg:grid-cols-3">
      {categories.map((board) => (
        <figure key={board.category} className="space-y-1">
          <figcaption className="text-sm">
            <span className="font-medium">{board.category}</span>
            <span className="text-muted-foreground">, {formatCountOf(board.votes, 'vote')}</span>
          </figcaption>
          <ForestPlot
            rows={board.standings.map((refit) => ({
              label: systemName(names, refit.system),
              refit,
            }))}
            labels={labels}
            domain={domain}
          />
        </figure>
      ))}
    </div>
  );
}
