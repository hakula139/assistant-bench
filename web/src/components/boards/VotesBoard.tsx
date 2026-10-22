import { cn } from 'cn';
import { TriangleAlert } from 'lucide-react';
import { useMemo } from 'react';

import { Filter } from '@/components/boards/Filter';
import {
  Leaderboard,
  leaderboardMarks,
  type LeaderboardRow,
} from '@/components/boards/Leaderboard';
import { CategoryForests } from '@/components/charts/CategoryForests';
import {
  ParetoScatter,
  paretoFrontier,
  type ScatterPoint,
} from '@/components/charts/ParetoScatter';
import { TableTwin } from '@/components/charts/TableTwin';
import { ForestLegend } from '@/components/site/Forest';
import { Section, Stat, note, stack, statList } from '@/components/site/PageParts';
import { Hint } from '@/components/site/Term';
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
import { terms } from '@/lib/glossary';
import type { CategoryBoard, Standing, VotesResults } from '@/lib/report';
import type { VotesRound } from '@/lib/reports';
import { systemName, type SystemNames } from '@/lib/systems';
import { useSearchParam } from '@/lib/use-search-param';

interface VotesBoardProps {
  family: string;
  /** Closed rounds, oldest first. */
  rounds: VotesRound[];
  names: SystemNames;
}

const ALL = 'all';

type Refit = CategoryBoard['standings'][number];

function leaderboardRows(
  results: VotesResults,
  category: string,
  names: SystemNames,
): LeaderboardRow[] {
  const row = (fit: Refit, standing: Standing | undefined): LeaderboardRow => ({
    system: fit.system,
    name: systemName(names, fit.system),
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
      : [{ system: row.system, name: row.name, x: value, score: row.score, ...row.interval }];
  });
}

function RoundSummary({ results, names }: { results: VotesResults; names: SystemNames }) {
  const { decision, rater_agreement: agreement } = results;
  const leaderNote = formatLeaderNote(decision);
  return (
    <div className="space-y-3">
      <dl className={statList}>
        <Stat label="Leader">
          {decision ? systemName(names, decision.leader) : MISSING}
          {leaderNote !== null && (
            <span className="block text-xs font-normal text-muted-foreground">{leaderNote}</span>
          )}
        </Stat>
        <Stat
          label={terms.votes.term}
          detail={`over ${formatCountOf(results.sessions, 'session')}${results.dropped_votes > 0 ? `, plus ${results.dropped_votes} left out of the fit` : ''}`}
        >
          {results.votes}
        </Stat>
        <Stat
          label={<Hint term={terms.raterAgreement} />}
          detail={`on ${formatCountOf(agreement.repeats, 'repeat')}`}
        >
          {formatPercent(agreement.agreement)}
        </Stat>
        <Stat label={<Hint term={terms.separatedPairs} />}>
          {formatPercent(results.separability)}
        </Stat>
        <Stat label={<Hint term={terms.splitHalf} />}>{formatTau(results.split_half_tau)}</Stat>
      </dl>
      {agreement.below_floor && (
        <p className={cn(note, 'flex gap-2')}>
          <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          Agreement below 70%, so the intervals overstate separation.
        </p>
      )}
    </div>
  );
}

/** A family's leaderboard, category forest plots, and cost and latency scatters for one round. */
export function VotesBoard({ family, rounds, names }: VotesBoardProps) {
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

  const rows = useMemo(
    () => leaderboardRows(results, activeCategory, names),
    [results, activeCategory, names],
  );
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
    .map((row) => row.name);
  const costFrontier = new Set(paretoFrontier(costPoints).map((point) => point.system));
  const latencyFrontier = new Set(paretoFrontier(latencyPoints).map((point) => point.system));
  const href = (system: string) => `/${family}/${system}/`;

  return (
    <TooltipProvider>
      <div className={stack}>
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

        <RoundSummary results={results} names={names} />

        <div className="space-y-4">
          <Leaderboard
            rows={rows}
            ranked={activeCategory === ALL}
            referenceSystem={results.reference}
            href={href}
          />
          {unranked.length > 0 && (
            <p className={note}>
              <Hint term={terms.unranked} />
              {` with too few comparisons to place them: ${unranked
                .map((system) => systemName(names, system))
                .toSorted((a, b) => a.localeCompare(b))
                .join(', ')}`}
            </p>
          )}
          <ForestLegend marks={leaderboardMarks(rows, results.reference)} />
        </div>

        {categories.length > 0 && (
          <Section title="Categories">
            <CategoryForests categories={results.categories} systems={order} names={names} />
            <TableTwin>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>System</TableHead>
                    {categories.map((name) => (
                      <TableHead key={name} numeric>
                        {name}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {order.map((system) => (
                    <TableRow key={system}>
                      <TableCell>{systemName(names, system)}</TableCell>
                      {results.categories.map((board) => {
                        const refit = board.standings.find((item) => item.system === system);
                        return (
                          <TableCell key={board.category} numeric>
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
                    <TableHead numeric>Score</TableHead>
                    <TableHead numeric>Cost / output</TableHead>
                    <TableHead numeric>p50 latency</TableHead>
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
                        <TableCell>{row.name}</TableCell>
                        <TableCell numeric>
                          {formatScore(row.score)} ({formatInterval(row.interval)})
                        </TableCell>
                        <TableCell numeric>{formatCost(row.standing.mean_cost_usd)}</TableCell>
                        <TableCell numeric>{formatSeconds(row.standing.p50_latency_sec)}</TableCell>
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
