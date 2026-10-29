import { useMemo } from 'react';

import { Filter } from '@/components/boards/Filter';
import {
  Leaderboard,
  leaderboardMarks,
  type LeaderboardRow,
} from '@/components/boards/Leaderboard';
import { SnapshotStatus } from '@/components/boards/SnapshotStatus';
import { CategoryForests, categoryMarks } from '@/components/charts/CategoryForests';
import {
  paretoFrontier,
  ParetoScatter,
  type ScatterPoint,
} from '@/components/charts/ParetoScatter';
import { TableTwin } from '@/components/charts/TableTwin';
import { ForestLegend } from '@/components/site/Forest';
import { note, Section, stack, Stat, statList } from '@/components/site/PageParts';
import { Hint, StatusMark } from '@/components/site/Term';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { unplacedText } from '@/lib/forest';
import {
  formatCost,
  formatCount,
  formatCountOf,
  formatLengthBias,
  formatScore,
  formatSeconds,
  formatTau,
  MISSING,
} from '@/lib/format';
import {
  categoryTerm,
  effectiveVotesTerm,
  fallbackAnchor,
  preliminaryTerm,
  anchorTerm as recordedAnchor,
  terms,
  type Term,
} from '@/lib/glossary';
import { roundMethod, type BoardLabels } from '@/lib/method';
import { systemPagePath } from '@/lib/paths';
import { isPreliminary } from '@/lib/ranking';
import type {
  CategoryBoard,
  CategoryStanding,
  Standing,
  VotesResults,
  VotesRules,
} from '@/lib/report';
import type { VotesRound } from '@/lib/reports';
import { reportNames, systemName, type SystemNames } from '@/lib/systems';
import { useSearchParam } from '@/lib/use-search-param';

interface VotesBoardProps {
  /** The board's path, which each system's page extends. */
  base: string;
  /** The closed round the board shows. */
  round: VotesRound;
  /** What the board calls its outputs and their figures, from `boardLabels`. */
  labels: BoardLabels;
}

const ALL = 'aggregate';
const categoryOption = (category: string) => `category:${category}`;

function leaderboardRows(
  base: string,
  results: VotesResults,
  category: string | null,
  names: SystemNames,
): LeaderboardRow[] {
  const row = (fit: CategoryStanding, standing: Standing | undefined): LeaderboardRow => ({
    system: fit.system,
    name: systemName(names, fit.system),
    href: standing ? systemPagePath(base, fit.system) : undefined,
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

function RoundSummary({ round, lengthBias }: { round: VotesRound; lengthBias: Term }) {
  const { results } = round;
  const { length } = results;

  return (
    <div className="flex flex-col gap-3">
      <dl className={statList}>
        <Stat
          label={terms.votes.term}
          detail={
            <>
              from {formatCountOf(results.voters, 'voter')}
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
        <Stat label={<Hint term={effectiveVotesTerm(results.method)} />}>
          {formatScore(results.effective_votes)}
        </Stat>
        <Stat label={<Hint term={terms.splitHalf} />}>{formatTau(results.split_half_tau)}</Stat>
        {length !== null && (
          <Stat label={<Hint term={lengthBias} />}>{formatLengthBias(length)}</Stat>
        )}
      </dl>
      <SnapshotStatus family={round.family} closedAt={round.closed_at} cutoffs={results.cutoffs} />
      <details className={note}>
        <summary>Round settings</summary>
        <p>{roundMethod(results.method, results.rules)}</p>
      </details>
    </div>
  );
}

/** One system's score in a category, with its vote count, or why the category has no score. */
function CategoryCell({
  board,
  system,
  rules,
}: {
  board: CategoryBoard;
  system: string;
  rules: VotesRules;
}) {
  const refit = board.standings.find((item) => item.system === system);

  return (
    <TableCell numeric>
      {refit ? (
        <span className="flex flex-col items-end leading-tight">
          <span className="flex items-center gap-1.5">
            {isPreliminary(refit, rules) && (
              <StatusMark status="preliminary" term={preliminaryTerm(rules)} compact />
            )}
            {formatScore(refit.score)}
          </span>
          <span className="text-xs text-muted-foreground">
            {formatCountOf(refit.votes, 'vote')}
          </span>
        </span>
      ) : (
        <span className="text-xs text-faint">
          {unplacedText[board.unranked.includes(system) ? 'unranked' : 'no-votes']}
        </span>
      )}
    </TableCell>
  );
}

/** A family's leaderboard, category forest plots, and cost and latency scatters for one round. */
export function VotesBoard({ base, round, labels }: VotesBoardProps) {
  const [category, setCategory] = useSearchParam('category', null);

  const { systems, results } = round;
  const names = useMemo(() => reportNames(systems), [systems]);
  const boards = results.categories;
  const activeCategory =
    category !== null && boards.some((board) => board.category === category) ? category : null;
  const unranked =
    activeCategory === null
      ? results.unranked
      : (boards.find((board) => board.category === activeCategory)?.unranked ?? []);

  const rows = useMemo(
    () => leaderboardRows(base, results, activeCategory, names),
    [base, results, activeCategory, names],
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
      {boards.length > 0 && (
        <Filter
          label="Category"
          value={activeCategory === null ? ALL : categoryOption(activeCategory)}
          onChange={(value) => {
            setCategory(value === ALL ? null : value.slice('category:'.length));
          }}
          options={[
            { value: ALL, label: 'All categories' },
            ...boards.map((board) => ({
              value: categoryOption(board.category),
              label: categoryTerm(board.category).term,
            })),
          ]}
        />
      )}

      <RoundSummary round={round} lengthBias={labels.lengthBias} />

      <div className="flex flex-col gap-4">
        <Leaderboard
          rows={rows}
          ranked={activeCategory === null}
          anchorSystem={results.anchor}
          anchorTerm={anchorTerm}
          preliminaryTerm={preliminaryTerm(results.rules)}
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

      {boards.length > 0 && (
        <Section title="Categories">
          <ForestLegend
            marks={categoryMarks(boards, results.method.anchor_score, results.rules)}
            anchorScore={results.method.anchor_score}
          />
          <CategoryForests
            categories={boards}
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
                  {boards.map((board) => (
                    <TableHead key={board.category} numeric>
                      {categoryTerm(board.category).term}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {order.map((system) => (
                  <TableRow key={system}>
                    <TableCell>{systemName(names, system)}</TableCell>
                    {boards.map((board) => (
                      <CategoryCell
                        key={board.category}
                        board={board}
                        system={system}
                        rules={results.rules}
                      />
                    ))}
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
