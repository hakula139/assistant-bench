import { cn } from 'cn';
import type { ReactNode } from 'react';

import {
  AnchorRule,
  CutArrow,
  ScoreDot,
  ScoreTrack,
  TrackAxis,
} from '@/components/site/ScoreTrack';
import { Hint } from '@/components/site/Term';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { ARROWHEAD, legendMarks, type ForestRow, type LegendMark } from '@/lib/forest';
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
  'anchor': 'no votes',
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

const legendKey: Record<LegendMark, { label: string; mark: ReactNode }> = {
  score: { label: 'Point score', mark: <ScoreDot preliminary={false} left="50%" /> },
  preliminary: { label: 'Preliminary', mark: <ScoreDot preliminary left="50%" /> },
  cut: {
    label: 'Score below the axis',
    mark: (
      <span
        className="absolute inset-y-0 left-1/2 -translate-x-1/2"
        style={{ width: ARROWHEAD.width }}
      >
        <CutArrow />
      </span>
    ),
  },
  anchor: { label: 'Anchor', mark: <AnchorRule left="50%" /> },
};

export function drawnMarks(
  rows: ForestRow[],
  domain: [number, number],
  anchorScore?: number,
): LegendMark[] {
  return legendMarks(rows, domain, anchorScore);
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
          <span aria-hidden="true" className="relative h-4 w-9 shrink-0">
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
  return (
    <Table className="table-fixed">
      <caption className="sr-only">{caption}</caption>
      <colgroup>
        <col className="w-2/5 sm:w-48" />
        <col />
        <col className="w-17 sm:w-24" />
      </colgroup>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead className="sr-only">System</TableHead>
          <TableHead className="h-6 p-0">
            <span className="sr-only">Point score axis</span>
            <TrackAxis domain={domain} anchorScore={anchorScore} />
          </TableHead>
          <TableHead className="sr-only">Score</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={row.system}>
            <TableHead scope="row" className="h-auto font-normal text-balance whitespace-normal">
              {row.href ? <a href={row.href}>{row.name}</a> : row.name}
            </TableHead>
            <TableCell className={cn('relative p-0', large ? 'h-11' : 'h-9')}>
              <span className="sr-only">{scoreText(row)}</span>
              <ScoreTrack
                domain={domain}
                anchorScore={anchorScore}
                estimate={
                  'estimate' in row
                    ? { score: row.estimate.score, preliminary: row.preliminary }
                    : undefined
                }
              />
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
        ))}
      </TableBody>
    </Table>
  );
}
