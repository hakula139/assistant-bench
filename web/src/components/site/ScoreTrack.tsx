import { cn } from 'cn';

import { ARROWHEAD, axisIntervals, clip, forestTicks, onAxis, sparseTicks } from '@/lib/forest';
import { formatScore } from '@/lib/format';

/** A placed system's point score, drawn hollow while its coverage is preliminary. */
interface TrackScore {
  score: number;
  preliminary: boolean;
}

const middle = 'absolute top-1/2 -translate-y-1/2';

/** The track's extent within its cell, inset by a dot's radius so a dot at either end stays whole. */
const extent = 'absolute inset-x-2 inset-y-0';

const rule = 'absolute inset-y-0 w-px';

/**
 * Hides a label on an axis of `axisIntervals` narrowest intervals where they would crowd, matching
 * the minimum tick spacing in `labelledTicks`.
 */
const axisFits: Record<number, string> = {
  1: '@max-[2.5rem]:hidden',
  2: '@max-[5rem]:hidden',
  3: '@max-[7.5rem]:hidden',
  4: '@max-[10rem]:hidden',
  5: '@max-[12.5rem]:hidden',
  6: '@max-[15rem]:hidden',
  7: '@max-[17.5rem]:hidden',
  8: '@max-[20rem]:hidden',
  9: '@max-[22.5rem]:hidden',
  10: '@max-[25rem]:hidden',
  11: '@max-[27.5rem]:hidden',
  12: '@max-[30rem]:hidden',
  13: '@max-[32.5rem]:hidden',
  14: '@max-[35rem]:hidden',
  15: '@max-[37.5rem]:hidden',
  16: '@max-[40rem]:hidden',
};

const position =
  ([start, stop]: [number, number]) =>
  (value: number) =>
    `${(((value - start) / (stop - start)) * 100).toFixed(2)}%`;

/** A point score, centered at `left` within a positioned parent. */
export function ScoreDot({ preliminary, left }: { preliminary: boolean; left: string }) {
  return (
    <span
      className={cn(
        middle,
        'size-3.5 -translate-x-1/2 rounded-full border-2',
        preliminary ? 'border-chart-series bg-background' : 'border-background bg-chart-series',
      )}
      style={{ left }}
    />
  );
}

/** The arrowhead for a score the axis cuts off, with its tip at the left of a positioned parent. */
export function CutArrow() {
  const { width, height } = ARROWHEAD;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      className={cn(middle, 'left-0 fill-chart-series')}
    >
      <path d={`M${width} 0 0 ${height / 2}l${width} ${height / 2}z`} />
    </svg>
  );
}

/** The rule at the anchor score, at `left` within a positioned parent. */
export function AnchorRule({ left }: { left: string }) {
  return <span className={cn(rule, 'bg-chart-reference')} style={{ left }} />;
}

/**
 * The score labels above a column of tracks on `domain`, keeping only the ends and an anchor
 * label where the column is too narrow for every tick's label.
 */
export function TrackAxis({
  domain,
  anchorScore,
}: {
  domain: [number, number];
  anchorScore?: number;
}) {
  const ticks = forestTicks(domain);
  const sparse = new Set(sparseTicks(domain, anchorScore));
  const intervals = axisIntervals(domain);
  const at = position(domain);

  return (
    <div aria-hidden="true" className="relative h-6">
      <div className={cn(extent, '@container')}>
        {ticks.map((tick) => (
          <span
            key={tick}
            className={cn(
              'absolute bottom-1 -translate-x-1/2 text-xs font-normal text-faint tabular-nums',
              !sparse.has(tick) && axisFits[intervals],
            )}
            style={{ left: at(tick) }}
          >
            {formatScore(tick)}
          </span>
        ))}
      </div>
    </div>
  );
}

/**
 * One system's score on a shared axis, with the axis gridlines and the anchor rule, filling a
 * positioned parent so that the rules of stacked rows join. A system without `estimate` leaves the
 * track empty.
 */
export function ScoreTrack({
  domain,
  anchorScore,
  estimate,
}: {
  domain: [number, number];
  anchorScore?: number;
  estimate?: TrackScore;
}) {
  const at = position(domain);
  const clipped = estimate && { ...clip(estimate, domain), preliminary: estimate.preliminary };

  return (
    <span aria-hidden="true" data-slot="score-track" className={extent}>
      {forestTicks(domain).map((tick) => (
        <span key={tick} className={cn(rule, 'bg-chart-grid')} style={{ left: at(tick) }} />
      ))}
      {onAxis(domain, anchorScore) && <AnchorRule left={at(anchorScore)} />}
      {clipped &&
        (clipped.offScale ? (
          <CutArrow />
        ) : (
          <ScoreDot preliminary={clipped.preliminary} left={at(clipped.score)} />
        ))}
    </span>
  );
}
