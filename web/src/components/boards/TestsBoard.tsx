import { Hint } from '@/components/site/Term';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { formatCost, formatPercent, formatSeconds } from '@/lib/format';
import { terms } from '@/lib/glossary';
import type { TestsRound } from '@/lib/reports';
import { systemName, type SystemNames } from '@/lib/systems';

interface TestsBoardProps {
  /** The latest closed round. */
  round: TestsRound;
  names: SystemNames;
}

/** A family's pass rates on its task tests in the latest round, with each column's meaning on tap. */
export function TestsBoard({ round: { round, results }, names }: TestsBoardProps) {
  const standings = results.standings.toSorted((a, b) => b.pass_rate - a.pass_rate);
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">Round {round}</p>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>System</TableHead>
            <TableHead numeric>
              <Hint term={terms.passRate} />
            </TableHead>
            <TableHead numeric>Trials</TableHead>
            <TableHead numeric>
              <Hint term={terms.errored} />
            </TableHead>
            <TableHead numeric>
              <Hint term={terms.testsCostLatency}>Cost / trial</Hint>
            </TableHead>
            <TableHead numeric>
              <Hint term={terms.testsCostLatency}>p50 latency</Hint>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {standings.map((standing) => (
            <TableRow key={standing.system}>
              <TableCell className="font-medium">{systemName(names, standing.system)}</TableCell>
              <TableCell numeric>{formatPercent(standing.pass_rate)}</TableCell>
              <TableCell numeric>{standing.trials}</TableCell>
              <TableCell numeric>{standing.errored}</TableCell>
              <TableCell numeric>{formatCost(standing.mean_cost_usd)}</TableCell>
              <TableCell numeric>{formatSeconds(standing.p50_latency_sec)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
