import { cn } from 'cn';
import type { ReactNode } from 'react';

import { Hint } from '@/components/site/Term';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  clip,
  forestTicks,
  legendMarks,
  sparseTicks,
  type ForestRow,
  type LegendMark,
} from '@/lib/forest';
import { formatScore } from '@/lib/format';
import type { Term } from '@/lib/glossary';

interface ForestProps {
  rows: ForestRow[];
  domain: [number, number];
  caption: string;
  anchorScore?: number;
  large?: boolean;
}

const unplacedText = {
  'anchor': 'unmeasured',
  'unranked': 'unranked',
  'no-votes': 'no votes',
};

function scoreText(row: ForestRow): string {
  return 'estimate' in row
    ? `${formatScore(row.estimate.score)}${row.preliminary ? ', preliminary' : ''}`
    : unplacedText[row.unplaced];
}

function details(row: ForestRow): Term {
  return {
    term: row.name,
    definition:
      'estimate' in row
        ? `Point score ${scoreText(row)}.`
        : row.unplaced === 'unranked'
          ? 'No comparisons connect this system to the ranked component.'
          : 'No round has measured this system yet.',
  };
}

const middle = 'absolute top-1/2 -translate-y-1/2';
const dot = cn(middle, 'size-3.5 -translate-x-1/2 rounded-full border-2');
const solidDot = 'border-background bg-chart-series';
const hollowDot = 'border-chart-series bg-background';
const anchorRule = 'bg-faint/40';

/** Matches the minimum tick spacing in `labelledTicks`. */
const axisFits: Record<number, string> = {
  1: '@max-[2.5rem]:hidden',
  2: '@max-[5rem]:hidden',
  3: '@max-[7.5rem]:hidden',
  4: '@max-[10rem]:hidden',
  5: '@max-[12.5rem]:hidden',
  6: '@max-[15rem]:hidden',
  7: '@max-[17.5rem]:hidden',
};

function Arrow() {
  return (
    <svg viewBox="0 0 6 10" className={cn(middle, 'left-0 h-2.5 w-1.5 fill-chart-series')}>
      <path d="M6 0 0 5l6 5z" />
    </svg>
  );
}

const legendKey: Record<LegendMark, { label: string; mark: ReactNode; width?: string }> = {
  score: { label: 'Point score', mark: <span className={cn(dot, solidDot, 'left-1/2')} /> },
  preliminary: { label: 'Preliminary', mark: <span className={cn(dot, hollowDot, 'left-1/2')} /> },
  cut: { label: 'Score below the axis', mark: <Arrow /> },
  anchor: {
    label: 'Anchor',
    mark: <span className={cn('absolute inset-0', anchorRule)} />,
    width: 'w-px',
  },
};

export function drawnMarks(
  rows: ForestRow[],
  domain: [number, number],
  anchorScore?: number,
): LegendMark[] {
  return legendMarks(
    rows,
    domain,
    anchorScore !== undefined && domain[0] <= anchorScore && anchorScore <= domain[1],
  );
}

export function ForestLegend({
  marks,
  className,
  anchorScore,
}: {
  marks: LegendMark[];
  className?: string;
  anchorScore?: number;
}) {
  if (marks.length === 0) {
    return null;
  }

  return (
    <ul
      aria-label="Legend"
      className={cn('flex flex-wrap gap-x-6 gap-y-2 text-sm text-muted-foreground', className)}
    >
      {marks.map((key) => (
        <li key={key} className="flex items-center gap-2">
          <span
            aria-hidden="true"
            className={cn('relative h-4 shrink-0', legendKey[key].width ?? 'w-9')}
          >
            {legendKey[key].mark}
          </span>
          {key === 'anchor' && anchorScore !== undefined
            ? `Anchor, ${formatScore(anchorScore)}`
            : legendKey[key].label}
        </li>
      ))}
    </ul>
  );
}

export function Forest({ rows, domain, caption, anchorScore, large = false }: ForestProps) {
  const ticks = forestTicks(domain);
  const sparse = new Set(sparseTicks(domain, anchorScore));
  const position = (value: number) =>
    `${(((value - domain[0]) / (domain[1] - domain[0])) * 100).toFixed(2)}%`;

  return (
    <Table className="table-fixed">
      <caption className="sr-only">{caption}</caption>
      <colgroup>
        <col className="w-2/5 sm:w-48" />
        <col />
        <col className="w-16 sm:w-24" />
      </colgroup>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead className="sr-only">System</TableHead>
          <TableHead className="h-6 p-0 font-normal">
            <span className="sr-only">Point score axis</span>
            <div aria-hidden="true" className="@container relative h-6">
              {ticks.map((tick) => (
                <span
                  key={tick}
                  className={cn(
                    'absolute bottom-1 -translate-x-1/2 text-xs text-faint tabular-nums',
                    !sparse.has(tick) && axisFits[ticks.length - 1],
                  )}
                  style={{ left: position(tick) }}
                >
                  {formatScore(tick)}
                </span>
              ))}
            </div>
          </TableHead>
          <TableHead className="sr-only">Score</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => {
          const clipped = 'estimate' in row ? clip(row.estimate, domain) : undefined;
          const preliminary = 'estimate' in row && row.preliminary;

          return (
            <TableRow key={row.system}>
              <TableHead scope="row" className="h-auto font-normal text-balance whitespace-normal">
                {row.href ? <a href={row.href}>{row.name}</a> : row.name}
              </TableHead>
              <TableCell className={cn('relative p-0', large ? 'h-11' : 'h-9')}>
                <span className="sr-only">{scoreText(row)}</span>
                <span aria-hidden="true">
                  {ticks.map((tick) => (
                    <span
                      key={tick}
                      className={cn('absolute inset-y-0 w-px', 'bg-chart-grid')}
                      style={{ left: position(tick) }}
                    />
                  ))}
                  {anchorScore !== undefined &&
                    domain[0] <= anchorScore &&
                    anchorScore <= domain[1] && (
                      <span
                        className={cn('absolute inset-y-0 w-px', anchorRule)}
                        style={{ left: position(anchorScore) }}
                      />
                    )}
                  {clipped &&
                    (clipped.offScale ? (
                      <Arrow />
                    ) : (
                      <span
                        className={cn(dot, preliminary ? hollowDot : solidDot)}
                        style={{ left: position(clipped.score) }}
                      />
                    ))}
                </span>
              </TableCell>
              <TableCell numeric>
                <Hint term={details(row)}>
                  {'estimate' in row ? (
                    formatScore(row.estimate.score)
                  ) : (
                    <span className="text-xs text-faint">{unplacedText[row.unplaced]}</span>
                  )}
                </Hint>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
