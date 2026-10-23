import type { ForestPlotRow } from '@/components/charts/ForestPlot';
import { Hint, StatusMark } from '@/components/site/Term';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  MISSING,
  formatCount,
  formatInterval,
  formatPercent,
  formatScore,
  formatSeconds,
} from '@/lib/format';
import { defectTerms, terms } from '@/lib/glossary';
import { defects, type Distribution, type HeadToHead, type Standing } from '@/lib/report';
import { systemName, type SystemNames } from '@/lib/systems';

// ── System Page Tables ──

export function HeadToHeadTable({
  headToHead,
  names,
}: {
  headToHead: HeadToHead[];
  names: SystemNames;
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Opponent</TableHead>
          <TableHead numeric>Votes</TableHead>
          <TableHead numeric>Wins</TableHead>
          <TableHead numeric>Losses</TableHead>
          <TableHead numeric>Ties</TableHead>
          <TableHead numeric>Both bad</TableHead>
          <TableHead numeric>Win rate</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {headToHead.map((pair) => (
          <TableRow key={pair.opponent}>
            <TableCell>{systemName(names, pair.opponent)}</TableCell>
            <TableCell numeric>{pair.votes}</TableCell>
            <TableCell numeric>{pair.wins}</TableCell>
            <TableCell numeric>{pair.losses}</TableCell>
            <TableCell numeric>{pair.ties}</TableCell>
            <TableCell numeric>{pair.both_bad}</TableCell>
            <TableCell numeric>{formatPercent(pair.win_rate)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export function CategoryTable({ categories }: { categories: ForestPlotRow[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Category</TableHead>
          <TableHead numeric>Score</TableHead>
          <TableHead numeric>95% interval</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {categories.map(({ label, refit }) => (
          <TableRow key={label}>
            <TableCell>{label}</TableCell>
            <TableCell numeric>{formatScore(refit.score)}</TableCell>
            <TableCell numeric>{formatInterval(refit.interval)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function formatDistribution(distribution: Distribution | null, format: (value: number) => string) {
  return distribution === null
    ? MISSING
    : `${format(distribution.p50)} (${format(distribution.p10)}–${format(distribution.p90)})`;
}

/** Lists every system's output tokens and latency as the median and the p10 to p90 range. */
export function DistributionTable({
  standings,
  system,
  names,
}: {
  standings: Standing[];
  system: string;
  names: SystemNames;
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>System</TableHead>
          <TableHead numeric>Output tokens</TableHead>
          <TableHead numeric>Latency</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {standings.map((item) => (
          <TableRow key={item.system} className={item.system === system ? 'font-medium' : ''}>
            <TableCell>{systemName(names, item.system)}</TableCell>
            <TableCell numeric>{formatDistribution(item.output_tokens, formatCount)}</TableCell>
            <TableCell numeric>{formatDistribution(item.latency_sec, formatSeconds)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export function DefectsTable({ standing }: { standing: Standing }) {
  return (
    <Table className="max-w-md">
      <TableHeader>
        <TableRow>
          <TableHead>Issue</TableHead>
          <TableHead numeric>Votes</TableHead>
          <TableHead numeric>Share</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {defects.map((defect) => {
          const count = standing.defects[defect] ?? 0;
          return (
            <TableRow key={defect}>
              <TableCell>
                <Hint term={defectTerms[defect]} />
              </TableCell>
              <TableCell numeric>{count}</TableCell>
              <TableCell numeric>
                {formatPercent(standing.votes === 0 ? null : count / standing.votes)}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

/** Lists the system's standing in each round it appeared in, given oldest first. */
export function RoundsTable({
  appearances,
  names,
}: {
  appearances: { round: string; reference: string; anchor: string; standing: Standing }[];
  names: SystemNames;
}) {
  return (
    <Table className="max-w-xl text-xs sm:text-sm">
      <TableHeader>
        <TableRow>
          <TableHead>Round</TableHead>
          <TableHead numeric>Score</TableHead>
          <TableHead numeric>95% interval</TableHead>
          <TableHead numeric>Votes</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {appearances.toReversed().map(({ round, reference, anchor, standing: past }) => (
          <TableRow key={round}>
            <TableCell>
              <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                {round}
                {anchor !== reference && (
                  <StatusMark
                    status="anchor"
                    term={{
                      term: terms.anchor.term,
                      definition: `This round fixed ${systemName(names, anchor)} at 1,000 because the reference, ${systemName(names, reference)}, is not ranked.`,
                    }}
                  />
                )}
              </span>
            </TableCell>
            <TableCell numeric>{formatScore(past.score)}</TableCell>
            <TableCell numeric>{formatInterval(past.interval)}</TableCell>
            <TableCell numeric>{past.votes}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
