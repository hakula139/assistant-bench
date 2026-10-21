import type { DotRange } from '@/components/charts/DotRangePlot';
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
import { flagLabels, flags, type Distribution, type HeadToHead, type Standing } from '@/lib/report';

// ── System Page Tables ──

export function HeadToHeadTable({ headToHead }: { headToHead: HeadToHead[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Opponent</TableHead>
          <TableHead className="text-right">Votes</TableHead>
          <TableHead className="text-right">Wins</TableHead>
          <TableHead className="text-right">Losses</TableHead>
          <TableHead className="text-right">Ties</TableHead>
          <TableHead className="text-right">Both bad</TableHead>
          <TableHead className="text-right">Win rate</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {headToHead.map((pair) => (
          <TableRow key={pair.opponent}>
            <TableCell>{pair.opponent}</TableCell>
            <TableCell className="text-right tabular-nums">{pair.votes}</TableCell>
            <TableCell className="text-right tabular-nums">{pair.wins}</TableCell>
            <TableCell className="text-right tabular-nums">{pair.losses}</TableCell>
            <TableCell className="text-right tabular-nums">{pair.ties}</TableCell>
            <TableCell className="text-right tabular-nums">{pair.both_bad}</TableCell>
            <TableCell className="text-right tabular-nums">
              {formatPercent(pair.win_rate)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export function CategoryTable({ categories }: { categories: DotRange[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Category</TableHead>
          <TableHead className="text-right">Score</TableHead>
          <TableHead className="text-right">95% interval</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {categories.map((row) => (
          <TableRow key={row.label}>
            <TableCell>{row.label}</TableCell>
            <TableCell className="text-right tabular-nums">{formatScore(row.value)}</TableCell>
            <TableCell className="text-right tabular-nums">
              {row.range ? formatInterval(row.range) : MISSING}
            </TableCell>
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
}: {
  standings: Standing[];
  system: string;
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>System</TableHead>
          <TableHead className="text-right">Output tokens</TableHead>
          <TableHead className="text-right">Latency</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {standings.map((item) => (
          <TableRow key={item.system} className={item.system === system ? 'font-medium' : ''}>
            <TableCell>{item.system}</TableCell>
            <TableCell className="text-right tabular-nums">
              {formatDistribution(item.output_tokens, formatCount)}
            </TableCell>
            <TableCell className="text-right tabular-nums">
              {formatDistribution(item.latency_sec, formatSeconds)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export function FlagsTable({ standing }: { standing: Standing }) {
  return (
    <Table className="max-w-md">
      <TableHeader>
        <TableRow>
          <TableHead>Flag</TableHead>
          <TableHead className="text-right">Votes</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {flags.map((flag) => (
          <TableRow key={flag}>
            <TableCell>{flagLabels[flag]}</TableCell>
            <TableCell className="text-right tabular-nums">{standing.flags[flag] ?? 0}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** Lists the system's standing in each round it appeared in, given oldest first. */
export function RoundsTable({
  appearances,
}: {
  appearances: { round: string; standing: Standing }[];
}) {
  return (
    <Table className="max-w-xl">
      <TableHeader>
        <TableRow>
          <TableHead>Round</TableHead>
          <TableHead className="text-right">Score</TableHead>
          <TableHead className="text-right">95% interval</TableHead>
          <TableHead className="text-right">Votes</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {appearances.toReversed().map(({ round, standing: past }) => (
          <TableRow key={round}>
            <TableCell>{round}</TableCell>
            <TableCell className="text-right tabular-nums">{formatScore(past.score)}</TableCell>
            <TableCell className="text-right tabular-nums">
              {formatInterval(past.interval)}
            </TableCell>
            <TableCell className="text-right tabular-nums">{past.votes}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
