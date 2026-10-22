import { cn } from 'cn';

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { REFERENCE_SCORE, clip, forestTicks, sparseTicks, type ForestRow } from '@/lib/forest';
import { formatInterval, formatScore } from '@/lib/format';

interface ForestProps {
  rows: ForestRow[];
  domain: [number, number];
  /** Names the table for assistive technology. */
  caption: string;
  /** Taller rows, for a family page. */
  large?: boolean;
}

const unplacedText = {
  'reference': {
    interval: 'Fixed at 1,000 as the reference',
    score: '1,000',
    summary: 'the reference, fixed at 1,000',
  },
  'unranked': {
    interval: 'Unbounded, too few comparisons to place it',
    score: 'unranked',
    summary: 'unranked, too few comparisons to place it',
  },
  'no-votes': { interval: 'Unbounded', score: 'no votes', summary: 'no votes yet' },
};

/** The interval cell's text, which screen readers read under the "95% interval" header. */
function intervalText(row: ForestRow): string {
  if ('estimate' in row) {
    return `${formatInterval(row.estimate.interval)}${row.preliminary ? ', preliminary' : ''}`;
  }
  return unplacedText[row.unplaced].interval;
}

/** The row's hover text, which names the system with its score and interval. */
function summary(row: ForestRow): string {
  if ('estimate' in row) {
    return `${row.name}: ${formatScore(row.estimate.score)}, 95% interval ${intervalText(row)}`;
  }
  return `${row.name}: ${unplacedText[row.unplaced].summary}`;
}

const middle = 'absolute top-1/2 -translate-y-1/2';
const dot = cn(middle, 'size-3.5 -translate-x-1/2 rounded-full border-2');
const solidDot = 'border-background bg-chart-series';
const hollowDot = 'border-chart-series bg-background';

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

/**
 * A forest plot drawn as a table without JavaScript: one row per system, with its interval on an
 * axis every row shares and its score in the last column. Positions along the axis are data, so
 * they are the only inline styles.
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
        <col className="w-40 sm:w-48" />
        <col />
        <col className="w-20 sm:w-24" />
      </colgroup>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead className="sr-only">System</TableHead>
          <TableHead className="relative h-6 p-0 font-normal">
            <span className="sr-only">95% interval</span>
            {ticks.map((tick) => (
              <span
                key={tick}
                aria-hidden="true"
                className={cn(
                  'absolute bottom-1 -translate-x-1/2 text-xs text-faint tabular-nums',
                  !sparse.has(tick) && 'max-sm:hidden',
                )}
                style={{ left: position(tick) }}
              >
                {formatScore(tick)}
              </span>
            ))}
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
            <TableRow key={row.system} title={summary(row)}>
              <TableHead scope="row" className="h-auto font-normal whitespace-normal">
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
                        tick === REFERENCE_SCORE ? 'bg-faint/40' : 'bg-chart-grid',
                      )}
                      style={{ left: position(tick) }}
                    />
                  ))}
                  {clipped ? (
                    <>
                      <span
                        className={cn(
                          middle,
                          'h-0 border-t-3 border-chart-series',
                          preliminary ? 'border-dashed' : 'border-solid',
                        )}
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
                  ) : unplaced === 'reference' ? (
                    <span
                      className={cn(dot, solidDot)}
                      style={{ left: position(REFERENCE_SCORE) }}
                    />
                  ) : (
                    <span className="text-faint">
                      <span
                        className={cn(
                          middle,
                          'inset-x-1.5 border-t-2 border-dashed border-current',
                        )}
                      />
                      <Arrow side="left" className="fill-current" />
                      <Arrow side="right" className="fill-current" />
                    </span>
                  )}
                </span>
              </TableCell>
              <TableCell numeric>
                {'estimate' in row ? (
                  formatScore(row.estimate.score)
                ) : unplaced === 'reference' ? (
                  unplacedText.reference.score
                ) : (
                  <span className="text-xs text-faint">{unplacedText[row.unplaced].score}</span>
                )}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
