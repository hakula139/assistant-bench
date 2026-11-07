import { useMemo } from 'react';

import { Filter } from '@/components/boards/Filter';
import { DotRangePlot } from '@/components/charts/DotRangePlot';
import { note } from '@/components/site/PageParts';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { decisionNote } from '@/lib/decision';
import { formatPercent, formatPercentRange } from '@/lib/format';
import type { CheckersReport } from '@/lib/reports';
import { reportNames, systemName } from '@/lib/systems';
import { useCategory } from '@/lib/use-category';

const rules = {
  'ellipsis': 'Three-period ellipsis',
  'han-punctuation': 'Fullwidth Chinese punctuation',
  'curly-quotes': 'Straight English quotes',
  'paragraph-lines': 'One source line per paragraph',
  'list-spacing': 'List spacing',
  'tree-comments': 'Aligned tree comments',
  'forbidden-marker': 'Forbidden marker omitted',
};

export function CheckersBoard({ round: { systems, results } }: { round: CheckersReport }) {
  const names = useMemo(() => reportNames(systems), [systems]);
  const [category, filter] = useCategory(results.categories.map((board) => board.category));
  const rows = (
    category === null
      ? results.standings
      : (results.categories.find((board) => board.category === category)?.standings ?? [])
  ).map((row) => ({ ...row, name: systemName(names, row.system) }));

  return (
    <div className="flex flex-col gap-6">
      {filter && <Filter {...filter} />}
      <DotRangePlot
        rows={rows.map((row) => ({
          label: row.name,
          value: row.pass_rate.rate,
          range: row.pass_rate,
        }))}
        label="Strict case pass rate with its 95% interval"
        format="percent"
        domain={[0, 1]}
      />
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>System</TableHead>
            <TableHead numeric>Pass rate</TableHead>
            <TableHead numeric>95% interval</TableHead>
            <TableHead numeric className="max-md:hidden">
              Cases
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.system}>
              <TableCell className="font-medium text-balance whitespace-normal sm:whitespace-nowrap">
                {row.name}
              </TableCell>
              <TableCell numeric>{formatPercent(row.pass_rate.rate)}</TableCell>
              <TableCell numeric>{formatPercentRange(row.pass_rate)}</TableCell>
              <TableCell numeric className="max-md:hidden">
                {row.cases}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {category === null && (
        <p className={note}>
          {decisionNote(results.decision, rows, names, {
            figure: 'strict case pass rate',
            test: 'a paired sign test over shared cases',
          })}
        </p>
      )}
      <section className="flex flex-col gap-4" aria-labelledby="prompt-effects">
        <h2 id="prompt-effects" className="text-xl font-semibold">
          Prompt effect
        </h2>
        <p className={note}>
          Prompted minus bare pass rate on paired cases. These descriptive differences do not
          establish significance. A missing interval means the paired case bootstrap could not
          estimate variation.
        </p>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Pair</TableHead>
              <TableHead numeric>Difference</TableHead>
              <TableHead numeric>95% interval</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {results.prompt_effects.map((pair) => (
              <TableRow key={pair.bare}>
                <TableCell className="whitespace-normal">
                  {systemName(names, pair.bare)}
                  <span className="block text-xs text-muted-foreground">
                    {systemName(names, pair.prompted)}
                  </span>
                </TableCell>
                <TableCell numeric>{formatPercent(pair.delta)}</TableCell>
                <TableCell numeric>
                  {pair.interval ? formatPercentRange(pair.interval) : '—'}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </section>
      <section className="flex flex-col gap-4" aria-labelledby="rule-diagnostics">
        <h2 id="rule-diagnostics" className="text-xl font-semibold">
          Rule diagnostics
        </h2>
        <p className={note}>
          Only applicable cases enter each rule's denominator. Every applicable case has one
          response, including failures. These rates do not contribute partial credit to the ranking.
        </p>
        {results.standings.map(({ system }) => (
          <details key={system} className="rounded-md border p-4">
            <summary className="cursor-pointer font-medium">{systemName(names, system)}</summary>
            <div className="mt-4">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Rule</TableHead>
                    <TableHead numeric>Passed / responses</TableHead>
                    <TableHead numeric>Cases</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {results.rules
                    .filter((row) => row.system === system)
                    .map((row) => (
                      <TableRow key={row.rule}>
                        <TableCell className="whitespace-normal">{rules[row.rule]}</TableCell>
                        <TableCell numeric>
                          {row.passed_responses} / {row.applicable_responses}
                        </TableCell>
                        <TableCell numeric>{row.applicable_cases}</TableCell>
                      </TableRow>
                    ))}
                </TableBody>
              </Table>
            </div>
          </details>
        ))}
      </section>
    </div>
  );
}
