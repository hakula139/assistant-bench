import { cn } from 'cn';
import { TriangleAlert } from 'lucide-react';
import { useMemo } from 'react';

import { Filter } from '@/components/boards/Filter';
import {
  Leaderboard,
  leaderboardMarks,
  type LeaderboardRow,
} from '@/components/boards/Leaderboard';
import { SnapshotStatus } from '@/components/boards/SnapshotStatus';
import { CategoryForests, categoryMarks } from '@/components/charts/CategoryForests';
import type { Refit } from '@/components/charts/ForestPlot';
import {
  paretoFrontier,
  ParetoScatter,
  type ScatterPoint,
} from '@/components/charts/ParetoScatter';
import { TableTwin } from '@/components/charts/TableTwin';
import { ForestLegend } from '@/components/site/Forest';
import { note, Section, stack, Stat, statList } from '@/components/site/PageParts';
import { Hint } from '@/components/site/Term';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  formatCost,
  formatCount,
  formatCountOf,
  formatLengthBias,
  formatPercent,
  formatScore,
  formatSeconds,
  formatTau,
  MISSING,
} from '@/lib/format';
import {
  agreementTerm,
  fallbackAnchor,
  inferenceNote,
  preliminaryTerm,
  anchorTerm as recordedAnchor,
  terms,
  type Term,
} from '@/lib/glossary';
import { roundMethod, type BoardLabels } from '@/lib/method';
import { systemPagePath } from '@/lib/paths';
import { isPreliminary } from '@/lib/ranking';
import type { Standing, VotesResults } from '@/lib/report';
import type { VotesRound } from '@/lib/reports';
import { reportNames, systemName, type SystemNames } from '@/lib/systems';
import { useSearchParam } from '@/lib/use-search-param';

interface VotesBoardProps {
  /** The board's path, which each system's page extends. */
  base: string;
  /** Closed rounds, oldest first. */
  rounds: VotesRound[];
  /** What the board calls its outputs and their figures, from `boardLabels`. */
  labels: BoardLabels;
}

const ALL = 'aggregate';
const categoryOption = (category: string) => `category:${category}`;

function leaderboardRows(
  results: VotesResults,
  category: string | null,
  names: SystemNames,
): LeaderboardRow[] {
  const row = (fit: Refit, standing: Standing | undefined): LeaderboardRow => ({
    system: fit.system,
    name: systemName(names, fit.system),
    standing,
    score: fit.score,
    votes: fit.votes,
    preliminary: isPreliminary(fit, results.rules),
  });

  if (category === null) {
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
      : [{ system: row.system, name: row.name, x: value, score: row.score }];
  });
}

function RoundSummary({ results, lengthBias }: { results: VotesResults; lengthBias: Term }) {
  const { rater_agreement: agreement, length } = results;

  return (
    <div className="flex flex-col gap-3">
      <dl className={statList}>
        <Stat
          label={terms.votes.term}
          detail={
            <>
              over {formatCountOf(results.sessions, 'session')}
              {results.dropped_votes > 0 && (
                <>
                  , <Hint term={terms.leftOut}>{formatCount(results.dropped_votes)} left out</Hint>
                </>
              )}
            </>
          }
        >
          {formatCount(results.votes)}
        </Stat>
        {results.method.human_weights.name === 'actor-proportional-cap' && (
          <>
            <Stat
              label="Accepted choices"
              detail="including repeats and choices excluded from this fit"
            >
              {formatCount(results.votes + results.dropped_votes)}
            </Stat>
            <Stat label="Effective vote mass" detail="after the complete family actor cap">
              {formatScore(results.effective_votes)}
            </Stat>
          </>
        )}
        <Stat
          label={<Hint term={agreementTerm(results.rules)} />}
          detail={`on ${formatCountOf(agreement.repeats, 'repeat')}`}
        >
          {formatPercent(agreement.agreement)}
        </Stat>
        <Stat label={<Hint term={terms.splitHalf} />}>{formatTau(results.split_half_tau)}</Stat>
        {length !== null && (
          <Stat label={<Hint term={lengthBias} />}>{formatLengthBias(length)}</Stat>
        )}
      </dl>
      <p className={note}>{inferenceNote}</p>
      {agreement.below_floor && (
        <p className={cn(note, 'flex gap-2')}>
          <TriangleAlert className="h-lh w-4 shrink-0" aria-hidden="true" />
          Repeat consistency is below the recorded{' '}
          {formatPercent(results.rules.rater_agreement_floor)} floor.
        </p>
      )}
    </div>
  );
}

/** A family's leaderboard, category forest plots, and cost and latency scatters for one round. */
export function VotesBoard({ base, rounds, labels }: VotesBoardProps) {
  const latest = rounds[rounds.length - 1];
  const [roundName, setRound] = useSearchParam('round', latest.round);
  const [category, setCategory] = useSearchParam('category', null);

  const {
    round: activeRound,
    systems,
    results,
  } = rounds.find((item) => item.round === roundName) ?? latest;
  const names = useMemo(() => reportNames(systems), [systems]);
  const categories = results.categories.map((board) => board.category);
  const activeCategory = category !== null && categories.includes(category) ? category : null;
  const unranked =
    activeCategory === null
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

  const href = (system: string) => systemPagePath(base, system);
  const anchorTerm =
    results.anchor === results.reference
      ? recordedAnchor(results.method)
      : fallbackAnchor(
          systemName(names, results.anchor),
          systemName(names, results.reference),
          results.method,
        );

  return (
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
            value={activeCategory === null ? ALL : categoryOption(activeCategory)}
            onChange={(value) => {
              setCategory(value === ALL ? null : value.slice('category:'.length));
            }}
            options={[
              { value: ALL, label: 'All categories' },
              ...categories.map((name) => ({ value: categoryOption(name), label: name })),
            ]}
          />
        )}
      </div>

      <RoundSummary results={results} lengthBias={labels.lengthBias} />
      {results.provenance?.status === 'ledger' && (
        <SnapshotStatus
          key={results.provenance.export_id}
          exportId={results.provenance.export_id}
          capturedAt={results.provenance.captured_at}
        />
      )}
      {results.method.human_weights.name === 'actor-proportional-cap' && (
        <p className={note}>
          Vote counts show raw ordinary coverage. Scores, win rates and both-bad rates use the
          recorded actor weights. Repeats contribute only to consistency diagnostics.
        </p>
      )}
      <details className={note}>
        <summary>Round settings</summary>
        <p>{roundMethod(results.method, results.rules)}</p>
      </details>

      <div className="flex flex-col gap-4">
        <Leaderboard
          rows={rows}
          ranked={activeCategory === null}
          anchorSystem={results.anchor}
          anchorTerm={anchorTerm}
          preliminaryTerm={preliminaryTerm(results.rules)}
          href={href}
          gateFailures={labels.gateFailures}
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
        <ForestLegend
          className="max-lg:hidden"
          marks={leaderboardMarks(rows, results.anchor)}
          anchorScore={results.method.anchor_score}
        />
      </div>

      {categories.length > 0 && (
        <Section title="Categories">
          <ForestLegend
            className="lg:hidden"
            marks={categoryMarks(results.categories, results.method.anchor_score, results.rules)}
            anchorScore={results.method.anchor_score}
          />
          <CategoryForests
            categories={results.categories}
            systems={order}
            names={names}
            anchorScore={results.method.anchor_score}
            rules={results.rules}
          />
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
                            ? `${formatScore(refit.score)}, ${formatCountOf(refit.votes, 'vote')}${isPreliminary(refit, results.rules) ? ', preliminary' : ''}`
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
          <p className="flex gap-2 text-sm text-muted-foreground">
            <span className="flex h-lh w-5 shrink-0 items-center" aria-hidden="true">
              <span className="h-0.5 w-full bg-faint" />
            </span>
            Pareto frontier: no other system scores higher for less
          </p>
          <div className="grid gap-6 lg:grid-cols-2">
            {costPoints.length > 0 && (
              <figure className="flex flex-col gap-1">
                <figcaption className="text-sm text-muted-foreground">
                  Mean cost per {labels.unit}
                  {unpriced.length > 0 && `, leaving out the unpriced ${unpriced.join(', ')}`}
                  {activeCategory !== null && ', over all categories'}
                </figcaption>
                <ParetoScatter
                  points={costPoints}
                  label={`Cost per ${labels.unit}`}
                  format={formatCost}
                />
              </figure>
            )}
            {latencyPoints.length > 0 && (
              <figure className="flex flex-col gap-1">
                <figcaption className="text-sm text-muted-foreground">
                  Median latency{activeCategory !== null && ', over all categories'}
                </figcaption>
                <ParetoScatter points={latencyPoints} label="p50 latency" format={formatSeconds} />
              </figure>
            )}
          </div>
          <TableTwin>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>System</TableHead>
                  <TableHead numeric>Score</TableHead>
                  <TableHead numeric>Cost / {labels.unit}</TableHead>
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
                      <TableCell numeric>{formatScore(row.score)}</TableCell>
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
  );
}
