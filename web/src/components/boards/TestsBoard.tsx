import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { formatCost, formatPercent, formatSeconds } from '@/lib/format';
import type { TestsRound } from '@/lib/reports';

interface TestsBoardProps {
  /** Closed rounds, oldest first. */
  rounds: TestsRound[];
}

/** A family's pass rates on its task tests in the latest round. */
export function TestsBoard({ rounds }: TestsBoardProps) {
  const { round, results } = rounds[rounds.length - 1];
  const standings = results.standings.toSorted((a, b) => b.pass_rate - a.pass_rate);
  return (
    <>
      <p className="mb-4 text-sm text-muted-foreground">Round {round}</p>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>System</TableHead>
            <TableHead numeric>Pass rate</TableHead>
            <TableHead numeric>Trials</TableHead>
            <TableHead numeric>Errored</TableHead>
            <TableHead numeric>Cost / trial</TableHead>
            <TableHead numeric>p50 latency</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {standings.map((standing) => (
            <TableRow key={standing.system}>
              <TableCell className="font-medium">{standing.system}</TableCell>
              <TableCell numeric>{formatPercent(standing.pass_rate)}</TableCell>
              <TableCell numeric>{standing.trials}</TableCell>
              <TableCell numeric>{standing.errored}</TableCell>
              <TableCell numeric>{formatCost(standing.mean_cost_usd)}</TableCell>
              <TableCell numeric>{formatSeconds(standing.p50_latency_sec)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </>
  );
}
