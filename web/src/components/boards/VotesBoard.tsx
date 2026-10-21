import { useMemo } from 'react';

import { Filter } from '@/components/boards/Filter';
import { Leaderboard, type LeaderboardRow } from '@/components/boards/Leaderboard';
import { CategoryHeatmap } from '@/components/charts/CategoryHeatmap';
import {
  ParetoScatter,
  paretoFrontier,
  type ScatterPoint,
} from '@/components/charts/ParetoScatter';
import { TableTwin } from '@/components/charts/TableTwin';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { TooltipProvider } from '@/components/ui/tooltip';
import {
  MISSING,
  formatCost,
  formatInterval,
  formatPercent,
  formatScore,
  formatSeconds,
  formatTau,
  formatTieBreak,
} from '@/lib/format';
import type { VotesResults } from '@/lib/report';
import type { VotesRound } from '@/lib/reports';
import { useSearchParam } from '@/lib/use-search-param';

interface VotesBoardProps {
  family: string;
  /** Closed rounds, oldest first. */
  rounds: VotesRound[];
}

const ALL = 'all';

function leaderboardRows(results: VotesResults, category: string): LeaderboardRow[] {
  if (category === ALL) {
    return results.standings.map((standing) => ({
      standing,
      score: standing.score,
      interval: standing.interval,
      votes: standing.votes,
      preliminary: standing.preliminary,
    }));
  }
  const board = results.categories.find((item) => item.category === category);
  const refits = new Map(board?.standings.map((refit) => [refit.system, refit]));
  return results.standings.flatMap((standing) => {
    const refit = refits.get(standing.system);
    return refit
      ? [
          {
            standing,
            score: refit.score,
            interval: refit.interval,
            votes: refit.votes,
            preliminary: refit.preliminary,
          },
        ]
      : [];
  });
}

function scatterPoints(rows: LeaderboardRow[], x: (row: LeaderboardRow) => number | null) {
  return rows.flatMap((row): ScatterPoint[] => {
    const value = x(row);
    return value === null
      ? []
      : [{ system: row.standing.system, x: value, score: row.score, ...row.interval }];
  });
}

function RoundSummary({ results }: { results: VotesResults }) {
  const { decision, rater_agreement: agreement } = results;
  const tieBreak = formatTieBreak(decision);
  return (
    <div className="space-y-3">
      <dl className="grid grid-cols-2 gap-x-8 gap-y-3 text-sm sm:flex sm:flex-wrap">
        <div>
          <dt className="text-muted-foreground">Leader</dt>
          <dd className="font-medium">
            {decision.leader}
            {tieBreak !== null && (
              <span className="block text-xs font-normal text-muted-foreground">{tieBreak}</span>
            )}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Votes</dt>
          <dd className="font-medium tabular-nums">
            {results.votes}{' '}
            <span className="font-normal text-muted-foreground">
              over {results.sessions} sessions
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Rater agreement</dt>
          <dd className="font-medium tabular-nums">
            {formatPercent(agreement.agreement)}{' '}
            <span className="font-normal text-muted-foreground">
              on {agreement.repeats} repeats
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Separated pairs</dt>
          <dd className="font-medium tabular-nums">{formatPercent(results.separability)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Split-half τ</dt>
          <dd className="font-medium tabular-nums">{formatTau(results.split_half_tau)}</dd>
        </div>
      </dl>
      {agreement.below_floor && (
        <p className="max-w-prose text-sm text-muted-foreground">
          Rater agreement is below the floor, so the separations are weaker than the intervals
          suggest.
        </p>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3 border-t pt-6">
      <h2 className="text-lg font-semibold">{title}</h2>
      {children}
    </section>
  );
}

/** A family's leaderboard, category heatmap, and cost and latency scatters for one round. */
export function VotesBoard({ family, rounds }: VotesBoardProps) {
  const latest = rounds[rounds.length - 1];
  const [roundName, setRound] = useSearchParam('round', latest.round);
  const [category, setCategory] = useSearchParam('category', ALL);

  const { round: activeRound, results } = rounds.find((item) => item.round === roundName) ?? latest;
  const categories = results.categories.map((board) => board.category);
  const activeCategory = categories.includes(category) ? category : ALL;

  const rows = useMemo(() => leaderboardRows(results, activeCategory), [results, activeCategory]);
  const reference = rows.find((row) => row.standing.system === results.reference)?.score;
  const byScore = useMemo(() => rows.toSorted((a, b) => b.score - a.score), [rows]);
  const order = byScore.map((row) => row.standing.system);
  const costPoints = useMemo(
    () => scatterPoints(rows, (row) => row.standing.mean_cost_usd),
    [rows],
  );
  const latencyPoints = useMemo(
    () => scatterPoints(rows, (row) => row.standing.p50_latency_sec),
    [rows],
  );
  const unpriced = rows
    .filter((row) => row.standing.mean_cost_usd === null)
    .map((row) => row.standing.system);
  const costFrontier = new Set(paretoFrontier(costPoints).map((point) => point.system));
  const latencyFrontier = new Set(paretoFrontier(latencyPoints).map((point) => point.system));
  const href = (system: string) => `/${family}/${system}/`;

  return (
    <TooltipProvider>
      <div className="space-y-8">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
          <Filter
            label="Round"
            value={activeRound}
            onChange={setRound}
            options={rounds.toReversed().map((item, i) => ({
              value: item.round,
              label: i === 0 ? `${item.round} (latest)` : item.round,
            }))}
          />
          {categories.length > 0 && (
            <Filter
              label="Category"
              value={activeCategory}
              onChange={setCategory}
              options={[
                { value: ALL, label: 'All categories' },
                ...categories.map((name) => ({ value: name, label: name })),
              ]}
            />
          )}
        </div>

        <RoundSummary results={results} />

        <Leaderboard
          rows={rows}
          ranked={activeCategory === ALL}
          reference={reference}
          href={href}
        />

        {categories.length > 0 && (
          <Section title="Categories">
            <CategoryHeatmap categories={results.categories} systems={order} />
            <TableTwin>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>System</TableHead>
                    {categories.map((name) => (
                      <TableHead key={name} className="text-right">
                        {name}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {order.map((system) => (
                    <TableRow key={system}>
                      <TableCell>{system}</TableCell>
                      {results.categories.map((board) => {
                        const refit = board.standings.find((item) => item.system === system);
                        return (
                          <TableCell key={board.category} className="text-right">
                            {refit
                              ? `${formatScore(refit.score)} (${formatInterval(refit.interval)}), ${refit.votes} votes${refit.preliminary ? ', preliminary' : ''}`
                              : MISSING}
                          </TableCell>
                        );
                      })}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableTwin>
          </Section>
        )}

        <Section title="Score against cost and latency">
          <p className="inline-flex items-center gap-2 text-sm text-muted-foreground">
            <span className="h-0.5 w-5 bg-faint" aria-hidden="true" />
            Pareto frontier: no other system scores higher for less
          </p>
          <div className="grid gap-6 lg:grid-cols-2">
            <figure className="space-y-1">
              <figcaption className="text-sm text-muted-foreground">
                Mean cost per output
                {unpriced.length > 0 && `, leaving out the unpriced ${unpriced.join(', ')}`}
                {activeCategory !== ALL && ', over all categories'}
              </figcaption>
              <ParetoScatter points={costPoints} label="Cost per output" format={formatCost} />
            </figure>
            <figure className="space-y-1">
              <figcaption className="text-sm text-muted-foreground">
                Median latency{activeCategory !== ALL && ', over all categories'}
              </figcaption>
              <ParetoScatter points={latencyPoints} label="p50 latency" format={formatSeconds} />
            </figure>
          </div>
          <TableTwin>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>System</TableHead>
                  <TableHead className="text-right">Score</TableHead>
                  <TableHead className="text-right">Cost / output</TableHead>
                  <TableHead className="text-right">p50 latency</TableHead>
                  <TableHead>On the frontier for</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {byScore.map((row) => {
                  const { system } = row.standing;
                  const frontiers = [
                    costFrontier.has(system) && 'cost',
                    latencyFrontier.has(system) && 'latency',
                  ].filter(Boolean);
                  return (
                    <TableRow key={system}>
                      <TableCell>{system}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatScore(row.score)} ({formatInterval(row.interval)})
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatCost(row.standing.mean_cost_usd)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatSeconds(row.standing.p50_latency_sec)}
                      </TableCell>
                      <TableCell>{frontiers.join(' and ') || MISSING}</TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </TableTwin>
        </Section>
      </div>
    </TooltipProvider>
  );
}
