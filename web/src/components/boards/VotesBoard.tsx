import { useMemo } from 'react';

import { Filter } from '@/components/boards/Filter';
import { Leaderboard, type LeaderboardRow } from '@/components/boards/Leaderboard';
import { CategoryForests } from '@/components/charts/CategoryForests';
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
  formatCountOf,
  formatInterval,
  formatPercent,
  formatScore,
  formatSeconds,
  formatLeaderNote,
  formatTau,
} from '@/lib/format';
import type { CategoryBoard, Standing, VotesResults } from '@/lib/report';
import type { VotesRound } from '@/lib/reports';
import { useSearchParam } from '@/lib/use-search-param';

interface VotesBoardProps {
  family: string;
  /** Closed rounds, oldest first. */
  rounds: VotesRound[];
}

const ALL = 'all';

type Refit = CategoryBoard['standings'][number];

function leaderboardRows(results: VotesResults, category: string): LeaderboardRow[] {
  const row = (fit: Refit, standing: Standing | undefined): LeaderboardRow => ({
    system: fit.system,
    standing,
    score: fit.score,
    interval: fit.interval,
    votes: fit.votes,
    preliminary: fit.preliminary,
  });
  if (category === ALL) {
    return results.standings.map((standing) => row(standing, standing));
  }
  const standings = new Map(results.standings.map((standing) => [standing.system, standing]));
  const board = results.categories.find((item) => item.category === category);
  return (board?.standings ?? []).map((refit) => row(refit, standings.get(refit.system)));
}

/** The family board's systems by score, then the systems only some category board ranks. */
function categorySystems(results: VotesResults): string[] {
  const ranked = results.standings.toSorted((a, b) => b.score - a.score);
  const refits = results.categories.flatMap((board) => board.standings);
  return [...new Set([...ranked, ...refits].map((item) => item.system))];
}

function scatterPoints(rows: LeaderboardRow[], x: (standing: Standing) => number | null) {
  return rows.flatMap((row): ScatterPoint[] => {
    const value = row.standing ? x(row.standing) : null;
    return value === null
      ? []
      : [{ system: row.system, x: value, score: row.score, ...row.interval }];
  });
}

function RoundSummary({ results }: { results: VotesResults }) {
  const { decision, rater_agreement: agreement } = results;
  const note = formatLeaderNote(decision);
  return (
    <div className="space-y-3">
      <dl className="grid grid-cols-2 gap-x-8 gap-y-3 text-sm sm:flex sm:flex-wrap">
        <div>
          <dt className="text-muted-foreground">Leader</dt>
          <dd className="font-medium">
            {decision?.leader ?? MISSING}
            {note !== null && (
              <span className="block text-xs font-normal text-muted-foreground">{note}</span>
            )}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Votes</dt>
          <dd className="font-medium tabular-nums">
            {results.votes}{' '}
            <span className="font-normal text-muted-foreground">
              {`over ${formatCountOf(results.sessions, 'session')}${results.dropped_votes > 0 ? `, plus ${results.dropped_votes} left out of the fit` : ''}`}
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Rater agreement</dt>
          <dd className="font-medium tabular-nums">
            {formatPercent(agreement.agreement)}{' '}
            <span className="font-normal text-muted-foreground">
              on {formatCountOf(agreement.repeats, 'repeat')}
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
          Rater agreement is below 70%, so the intervals overstate how well systems separate.
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

/** A family's leaderboard, category forest plots, and cost and latency scatters for one round. */
export function VotesBoard({ family, rounds }: VotesBoardProps) {
  const latest = rounds[rounds.length - 1];
  const [roundName, setRound] = useSearchParam('round', latest.round);
  const [category, setCategory] = useSearchParam('category', ALL);

  const { round: activeRound, results } = rounds.find((item) => item.round === roundName) ?? latest;
  const categories = results.categories.map((board) => board.category);
  const activeCategory = categories.includes(category) ? category : ALL;
  const unranked =
    activeCategory === ALL
      ? results.unranked
      : (results.categories.find((board) => board.category === activeCategory)?.unranked ?? []);

  const rows = useMemo(() => leaderboardRows(results, activeCategory), [results, activeCategory]);
  const reference = rows.find((row) => row.system === results.reference)?.score;
  const order = useMemo(() => categorySystems(results), [results]);
  const measured = useMemo(
    () =>
      rows
        .toSorted((a, b) => b.score - a.score)
        .flatMap((row) => (row.standing ? [{ ...row, standing: row.standing }] : [])),
    [rows],
  );
  const costPoints = useMemo(
    () => scatterPoints(rows, (standing) => standing.mean_cost_usd),
    [rows],
  );
  const latencyPoints = useMemo(
    () => scatterPoints(rows, (standing) => standing.p50_latency_sec),
    [rows],
  );
  const unpriced = measured
    .filter((row) => row.standing.mean_cost_usd === null)
    .map((row) => row.system);
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
        {unranked.length > 0 && (
          <p className="-mt-4 max-w-prose text-sm text-muted-foreground">
            {`Unranked until more comparisons connect them to the reference: ${unranked.join(', ')}.`}
          </p>
        )}

        {categories.length > 0 && (
          <Section title="Categories">
            <CategoryForests categories={results.categories} systems={order} />
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
                              ? `${formatScore(refit.score)} (${formatInterval(refit.interval)}), ${formatCountOf(refit.votes, 'vote')}${refit.preliminary ? ', preliminary' : ''}`
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

        {(costPoints.length > 0 || latencyPoints.length > 0) && (
          <Section title="Score against cost and latency">
            <p className="inline-flex items-center gap-2 text-sm text-muted-foreground">
              <span className="h-0.5 w-5 bg-faint" aria-hidden="true" />
              Pareto frontier: no other system scores higher for less
            </p>
            <div className="grid gap-6 lg:grid-cols-2">
              {costPoints.length > 0 && (
                <figure className="space-y-1">
                  <figcaption className="text-sm text-muted-foreground">
                    Mean cost per output
                    {unpriced.length > 0 && `, leaving out the unpriced ${unpriced.join(', ')}`}
                    {activeCategory !== ALL && ', over all categories'}
                  </figcaption>
                  <ParetoScatter points={costPoints} label="Cost per output" format={formatCost} />
                </figure>
              )}
              {latencyPoints.length > 0 && (
                <figure className="space-y-1">
                  <figcaption className="text-sm text-muted-foreground">
                    Median latency{activeCategory !== ALL && ', over all categories'}
                  </figcaption>
                  <ParetoScatter
                    points={latencyPoints}
                    label="p50 latency"
                    format={formatSeconds}
                  />
                </figure>
              )}
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
                  {measured.map((row) => {
                    const { system } = row;
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
        )}
      </div>
    </TooltipProvider>
  );
}
