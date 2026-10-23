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
import { formatInterval, formatScore } from '@/lib/format';
import type { Term } from '@/lib/glossary';
import { ANCHOR_SCORE } from '@/lib/ranking';

interface ForestProps {
  rows: ForestRow[];
  domain: [number, number];
  /** Names the table for assistive technology. */
  caption: string;
  /** Taller rows, for a family page. */
  large?: boolean;
}

const anchor = formatScore(ANCHOR_SCORE);

const unplacedText = {
  'anchor': {
    interval: `Fixed at ${anchor} as the anchor`,
    score: anchor,
    details: `The anchor, fixed at ${anchor}.`,
  },
  'unranked': {
    interval: 'Unbounded, too few comparisons to place it',
    score: 'unranked',
    details: 'Unranked, with too few comparisons to place it.',
  },
  'no-votes': { interval: 'Unbounded', score: 'no votes', details: 'No votes yet.' },
};

/** The interval cell's text, which screen readers read under the "95% interval" header. */
function intervalText(row: ForestRow): string {
  if ('estimate' in row) {
    return `${formatInterval(row.estimate.interval)}${row.preliminary ? ', preliminary' : ''}`;
  }
  return unplacedText[row.unplaced].interval;
}

/** The row's details, which its score opens on tap, click, or Enter. */
function details(row: ForestRow): Term {
  return {
    term: row.name,
    definition:
      'estimate' in row
        ? `Score ${formatScore(row.estimate.score)}, 95% interval ${intervalText(row)}.`
        : unplacedText[row.unplaced].details,
  };
}

const middle = 'absolute top-1/2 -translate-y-1/2';
const dot = cn(middle, 'size-3.5 -translate-x-1/2 rounded-full border-2');
const solidDot = 'border-background bg-chart-series';
const hollowDot = 'border-chart-series bg-background';
const line = cn(middle, 'h-0 border-t-3 border-chart-series');
/** The rule a forest plot draws at the anchor score, matching `anchorRule` on the Plot charts. */
const anchorRule = 'bg-faint/40';

/** An arrowhead at one end of the track, pointing off the axis. */
function Arrow({ side, className }: { side: 'left' | 'right'; className: string }) {
  return (
    <svg
      viewBox="0 0 6 10"
      className={cn(middle, 'h-2.5 w-1.5', side === 'left' ? 'left-0' : 'right-0', className)}
    >
      <path d={side === 'left' ? 'M6 0 0 5l6 5z' : 'M0 0l6 5-6 5z'} />
    </svg>
  );
}

/** The dashed track with an arrowhead at both ends that a row without an estimate draws. */
function Unbounded() {
  return (
    <span className="text-faint">
      <span className={cn(middle, 'inset-x-1.5 border-t-2 border-dashed border-current')} />
      <Arrow side="left" className="fill-current" />
      <Arrow side="right" className="fill-current" />
    </span>
  );
}

const legendKey: Record<LegendMark, { label: string; mark: ReactNode; width?: string }> = {
  score: {
    label: 'Score, 95% interval',
    mark: (
      <>
        <span className={cn(line, 'inset-x-0 border-solid')} />
        <span className={cn(dot, solidDot, 'left-1/2')} />
      </>
    ),
  },
  preliminary: {
    label: 'Preliminary',
    mark: (
      <>
        <span className={cn(line, 'inset-x-0 border-dashed')} />
        <span className={cn(dot, hollowDot, 'left-1/2')} />
      </>
    ),
  },
  cut: {
    label: 'Interval past the axis',
    mark: (
      <>
        <Arrow side="left" className="fill-chart-series" />
        <span className={cn(line, 'right-0 left-1.5 border-solid')} />
      </>
    ),
  },
  anchor: {
    label: `Anchor, ${anchor}`,
    mark: <span className={cn('absolute inset-0', anchorRule)} />,
    width: 'w-px',
  },
  unbounded: { label: 'Unbounded', mark: <Unbounded /> },
};

/** Returns the marks a `Forest` of `rows` on `domain` draws, for its legend. */
export function drawnMarks(rows: ForestRow[], domain: [number, number]): LegendMark[] {
  return legendMarks(rows, domain, forestTicks(domain).includes(ANCHOR_SCORE));
}

/** A key to the interval marks, for the `marks` a nearby forest plot draws. */
export function ForestLegend({ marks }: { marks: LegendMark[] }) {
  return (
    <ul
      aria-label="Legend"
      className="flex flex-wrap gap-x-6 gap-y-2 text-sm text-muted-foreground"
    >
      {marks.map((key) => (
        <li key={key} className="flex items-center gap-2">
          <span
            aria-hidden="true"
            className={cn('relative h-4 shrink-0', legendKey[key].width ?? 'w-9')}
          >
            {legendKey[key].mark}
          </span>
          {legendKey[key].label}
        </li>
      ))}
    </ul>
  );
}

/**
 * A forest plot drawn as a table: one row per system, with its interval on a shared axis and its
 * score in the last column, which opens the row's details once hydrated. Positions along the axis
 * are data, so they are the only inline styles.
 */
export function Forest({ rows, domain, caption, large = false }: ForestProps) {
  const ticks = forestTicks(domain);
  const sparse = new Set(sparseTicks(domain));
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
            <span className="sr-only">95% interval</span>
            <div aria-hidden="true" className="@container relative h-6">
              {ticks.map((tick) => (
                <span
                  key={tick}
                  className={cn(
                    'absolute bottom-1 -translate-x-1/2 text-xs text-faint tabular-nums',
                    !sparse.has(tick) && '@max-axis:hidden',
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
          const unplaced = 'unplaced' in row ? row.unplaced : undefined;
          return (
            <TableRow key={row.system}>
              <TableHead scope="row" className="h-auto font-normal text-balance whitespace-normal">
                {row.href ? <a href={row.href}>{row.name}</a> : row.name}
              </TableHead>
              <TableCell className={cn('relative p-0', large ? 'h-11' : 'h-9')}>
                <span className="sr-only">{intervalText(row)}</span>
                <span aria-hidden="true">
                  {ticks.map((tick) => (
                    <span
                      key={tick}
                      className={cn(
                        'absolute inset-y-0 w-px',
                        tick === ANCHOR_SCORE ? anchorRule : 'bg-chart-grid',
                      )}
                      style={{ left: position(tick) }}
                    />
                  ))}
                  {clipped ? (
                    <>
                      <span
                        className={cn(line, preliminary ? 'border-dashed' : 'border-solid')}
                        style={{
                          left: position(clipped.lower),
                          right: `calc(100% - ${position(clipped.upper)})`,
                        }}
                      />
                      {clipped.cutLow && <Arrow side="left" className="fill-chart-series" />}
                      {!clipped.offScale && (
                        <span
                          className={cn(dot, preliminary ? hollowDot : solidDot)}
                          style={{ left: position(clipped.score) }}
                        />
                      )}
                    </>
                  ) : unplaced === 'anchor' ? (
                    <span className={cn(dot, solidDot)} style={{ left: position(ANCHOR_SCORE) }} />
                  ) : (
                    <Unbounded />
                  )}
                </span>
              </TableCell>
              <TableCell numeric>
                <Hint term={details(row)}>
                  {'estimate' in row ? (
                    formatScore(row.estimate.score)
                  ) : unplaced === 'anchor' ? (
                    unplacedText.anchor.score
                  ) : (
                    <span className="text-xs text-faint">{unplacedText[row.unplaced].score}</span>
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
