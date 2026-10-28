import { useMemo } from 'react';

import { Filter } from '@/components/boards/Filter';
import { DotRangePlot, type DotRange } from '@/components/charts/DotRangePlot';
import { note } from '@/components/site/PageParts';
import { Hint } from '@/components/site/Term';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { formatCost, formatPercent, formatPercentRange, formatSeconds } from '@/lib/format';
import { passHatKTerm, terms } from '@/lib/glossary';
import type { PassRate, TestsResults, TestsStanding } from '@/lib/report';
import type { TestsRound } from '@/lib/reports';
import { reportNames, systemName, type SystemNames } from '@/lib/systems';
import { useSearchParam } from '@/lib/use-search-param';

interface TestsBoardProps {
  /** The closed round the board shows. */
  round: TestsRound;
}

/** One system's pass rate on the family's tasks or a category's. */
interface TestsRow {
  system: string;
  name: string;
  tasks: number;
  pass_rate: PassRate;
  /** The family-wide standing, which only the family view shows. */
  standing?: TestsStanding;
}

const ALL = 'aggregate';
const RATE_DOMAIN: [number, number] = [0, 1];
const wide = 'max-md:hidden';

function testsRows(results: TestsResults, category: string | null, names: SystemNames): TestsRow[] {
  const row = (
    { system, tasks, pass_rate }: Omit<TestsRow, 'name' | 'standing'>,
    standing?: TestsStanding,
  ): TestsRow => ({ system, name: systemName(names, system), tasks, pass_rate, standing });
  const rows =
    category === null
      ? results.standings.map((standing) => row(standing, standing))
      : (results.categories.find((board) => board.category === category)?.standings ?? []).map(
          (refit) => row(refit),
        );

  return rows.toSorted(
    (a, b) => b.pass_rate.rate - a.pass_rate.rate || a.name.localeCompare(b.name),
  );
}

const allOf = new Intl.ListFormat('en', { type: 'conjunction' });

/**
 * Names the top pass rate and the systems the paired sign test cannot separate from it, in board
 * order.
 */
function decisionNote(
  { leader, tied }: TestsResults['decision'],
  rows: TestsRow[],
  names: SystemNames,
): string {
  const top = systemName(names, leader);
  if (tied.length === 0) {
    return `${top} has the top pass rate, and a paired sign test over shared tasks separates it from every other system.`;
  }

  const others = rows.filter((row) => tied.includes(row.system)).map((row) => row.name);
  return `${top} has the top pass rate, but a paired sign test over shared tasks cannot separate it from ${allOf.format(others)}.`;
}

/**
 * A family's pass rates on its task tests in one round, drawn with their 95% intervals, with each
 * column's meaning on tap.
 */
export function TestsBoard({ round: { systems, results } }: TestsBoardProps) {
  const [category, setCategory] = useSearchParam('category', null);
  const names = useMemo(() => reportNames(systems), [systems]);
  const categories = results.categories.map((board) => board.category);
  const activeCategory = category !== null && categories.includes(category) ? category : null;
  const family = activeCategory === null;

  const rows = useMemo(
    () => testsRows(results, activeCategory, names),
    [results, activeCategory, names],
  );
  const ranges = rows.map((row): DotRange => ({
    label: row.name,
    value: row.pass_rate.rate,
    range: row.pass_rate,
  }));
  const passHatK = results.samples > 1 ? passHatKTerm(results.samples) : null;

  return (
    <div className="flex flex-col gap-4">
      {categories.length > 1 && (
        <Filter
          label="Category"
          value={activeCategory ?? ALL}
          onChange={(value) => {
            setCategory(value === ALL ? null : value);
          }}
          options={[
            { value: ALL, label: 'All categories' },
            ...categories.map((name) => ({ value: name, label: name })),
          ]}
        />
      )}
      <DotRangePlot
        rows={ranges}
        label="Pass rate with its 95% interval"
        format="percent"
        domain={RATE_DOMAIN}
      />
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>System</TableHead>
            <TableHead numeric>
              <Hint term={terms.passRate} />
            </TableHead>
            <TableHead numeric>
              <Hint term={terms.passInterval} />
            </TableHead>
            {family && passHatK && (
              <TableHead numeric>
                <Hint term={passHatK} />
              </TableHead>
            )}
            <TableHead numeric className={wide}>
              <Hint term={terms.tasks} />
            </TableHead>
            {family && (
              <>
                <TableHead numeric className={wide}>
                  Trials
                </TableHead>
                <TableHead numeric className={wide}>
                  <Hint term={terms.errored} />
                </TableHead>
                <TableHead numeric className={wide}>
                  <Hint term={terms.testsCostLatency}>Cost / trial</Hint>
                </TableHead>
                <TableHead numeric className={wide}>
                  <Hint term={terms.testsCostLatency}>p50 latency</Hint>
                </TableHead>
              </>
            )}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map(({ system, name, tasks, pass_rate, standing }) => (
            <TableRow key={system}>
              <TableCell className="font-medium text-balance whitespace-normal sm:whitespace-nowrap">
                {name}
              </TableCell>
              <TableCell numeric>{formatPercent(pass_rate.rate)}</TableCell>
              <TableCell numeric>{formatPercentRange(pass_rate)}</TableCell>
              {standing && passHatK && (
                <TableCell numeric>{formatPercent(standing.pass_hat_k)}</TableCell>
              )}
              <TableCell numeric className={wide}>
                {tasks}
              </TableCell>
              {standing && (
                <>
                  <TableCell numeric className={wide}>
                    {standing.trials}
                  </TableCell>
                  <TableCell numeric className={wide}>
                    {standing.errored}
                  </TableCell>
                  <TableCell numeric className={wide}>
                    {formatCost(standing.mean_cost_usd)}
                  </TableCell>
                  <TableCell numeric className={wide}>
                    {formatSeconds(standing.p50_latency_sec)}
                  </TableCell>
                </>
              )}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {family && results.standings.length > 1 && (
        <p className={note}>{decisionNote(results.decision, rows, names)}</p>
      )}
    </div>
  );
}
