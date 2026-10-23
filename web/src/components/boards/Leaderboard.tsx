import * as Plot from '@observablehq/plot';
import {
  createColumnHelper,
  createSortedRowModel,
  metaHelper,
  rowSortingFeature,
  sortFn_alphanumeric,
  sortFn_basic,
  tableFeatures,
  useTable,
} from '@tanstack/react-table';
import { cn } from 'cn';
import { ArrowDown, ArrowUp, CircleHelp } from 'lucide-react';
import { useCallback, useMemo } from 'react';

import { anchorRule, forestMarks } from '@/components/charts/forest-marks';
import { PlotFigure, type PlotFrame } from '@/components/charts/PlotFigure';
import { Hint, StatusMark } from '@/components/site/Term';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  clip,
  forestDomain,
  forestTicks,
  labelledTicks,
  legendMarks,
  type LegendMark,
} from '@/lib/forest';
import {
  formatCountOf,
  formatInterval,
  formatPercent,
  formatRank,
  formatScore,
  MISSING,
} from '@/lib/format';
import { terms, type Term } from '@/lib/glossary';
import type { Interval, Standing } from '@/lib/report';

/**
 * A system's row, whose score fields come from the board in view: the family's, or a category's
 * refit, which carries no rank spread.
 */
export interface LeaderboardRow {
  system: string;
  /** The system's display name, which the row shows and sorts by. */
  name: string;
  /**
   * The system's standing on the family board, which also holds its family-wide rates, cost, and
   * latency. A category can rank a system that the family board leaves unranked.
   */
  standing: Standing | undefined;
  score: number;
  interval: Interval;
  votes: number;
  preliminary: boolean;
}

interface LeaderboardProps {
  rows: LeaderboardRow[];
  /** Whether the rows are the family board's, with its ranks, or a category's refits. */
  ranked: boolean;
  /** The system the scores are relative to, marked by name and ruled on the axis at its score. */
  anchorSystem: string;
  /** The mark's definition, which names the reference when the anchor stands in for it. */
  anchorTerm: Term;
  /** Links a system name to its page, which only systems with a family standing have. */
  href: (system: string) => string;
  /** The gate-failure column's label and meaning. */
  gateTerm: Term;
}

const ROW_HEIGHT = 48;
const FOREST_MARGIN = 22;

interface ColumnMeta {
  numeric?: boolean;
  forest?: boolean;
  wraps?: boolean;
  term?: Term;
  /** A figure that narrow screens leave out and the row's score opens instead. */
  detail?: boolean;
}

const features = tableFeatures({
  columnMeta: metaHelper<ColumnMeta>(),
  rowSortingFeature,
  sortedRowModel: createSortedRowModel(),
  sortFns: { alphanumeric: sortFn_alphanumeric, basic: sortFn_basic },
});
const helper = createColumnHelper<typeof features, LeaderboardRow>();

/** The shared x scale, so the header axis and every row's interval line up. */
function forestScale(domain: [number, number]): Plot.ScaleOptions {
  return { domain, insetLeft: FOREST_MARGIN, insetRight: FOREST_MARGIN };
}

function ForestAxis({ domain }: { domain: [number, number] }) {
  const plot = useCallback(
    ({ width }: PlotFrame): Plot.PlotOptions => ({
      margin: 0,
      marginTop: 20,
      x: {
        ...forestScale(domain),
        axis: 'top',
        ticks: labelledTicks(domain, width - 2 * FOREST_MARGIN),
        tickFormat: formatScore,
        tickSize: 0,
        label: null,
      },
      marks: [],
    }),
    [domain],
  );
  return <PlotFigure plot={plot} height={28} />;
}

function IntervalCell({
  row,
  domain,
  anchor,
}: {
  row: LeaderboardRow;
  domain: [number, number];
  anchor: number | undefined;
}) {
  const plot = useCallback(
    (): Plot.PlotOptions => ({
      margin: 0,
      x: { ...forestScale(domain), axis: null },
      y: { axis: null, domain: [0, 1] },
      color: { type: 'identity' },
      marks: [
        Plot.gridX(forestTicks(domain), { stroke: 'var(--chart-grid)', strokeOpacity: 1 }),
        anchor === undefined ? null : anchorRule(anchor),
        forestMarks(
          [{ y: 0.5, score: row.score, clipped: clip(row, domain), preliminary: row.preliminary }],
          domain,
        ),
      ],
    }),
    [row, domain, anchor],
  );

  const summary = `${row.name}: ${formatScore(row.score)}, 95% interval ${formatInterval(row.interval)}`;
  return (
    <div role="img" aria-label={summary} title={summary}>
      <PlotFigure plot={plot} height={ROW_HEIGHT} />
    </div>
  );
}

function SortIcon({ direction }: { direction: false | 'asc' | 'desc' }) {
  if (direction === 'asc') {
    return <ArrowUp className="size-3.5" aria-hidden="true" />;
  }
  if (direction === 'desc') {
    return <ArrowDown className="size-3.5" aria-hidden="true" />;
  }
  return null;
}

/**
 * The figures of a row's `detail` columns, which its score opens on tap, click, or Enter, since
 * narrow screens leave those columns out.
 */
function rowDetails(row: LeaderboardRow, ranked: boolean, gateTerm: Term): Term {
  const rates = `${terms.bothBad.term.toLowerCase()} ${formatPercent(row.standing?.both_bad_rate ?? null)}, ${gateTerm.term.toLowerCase()} ${formatPercent(row.standing?.gate_failure_rate ?? null)}`;
  return {
    term: row.name,
    definition: `${formatCountOf(row.votes, 'vote')}, ${rates}${ranked ? '' : ' across all categories'}.`,
  };
}

const anchorScore = (rows: LeaderboardRow[], anchorSystem: string) =>
  rows.find((row) => row.system === anchorSystem)?.score;

/** Returns the marks a `Leaderboard` of `rows` draws, for its legend. */
export function leaderboardMarks(rows: LeaderboardRow[], anchorSystem: string): LegendMark[] {
  return legendMarks(
    rows.map((row) => ({
      system: row.system,
      name: row.name,
      estimate: row,
      preliminary: row.preliminary,
    })),
    forestDomain(rows),
    anchorScore(rows, anchorSystem) !== undefined,
  );
}

/**
 * The leaderboard table, whose interval column is a forest plot on one shared axis. Below `lg` the
 * table leaves that column out, keeping each interval as text under its score, and below `md` it
 * also leaves out the `detail` columns, which each score opens, so the table fits without scrolling.
 */
export function Leaderboard({
  rows,
  ranked,
  anchorSystem,
  anchorTerm,
  href,
  gateTerm,
}: LeaderboardProps) {
  const domain = useMemo(() => forestDomain(rows), [rows]);
  const anchor = anchorScore(rows, anchorSystem);

  const columns = useMemo(() => {
    const familyWide = (label: string) =>
      ranked
        ? label
        : () => (
            <span className="flex flex-col items-end leading-tight">
              {label}
              <span className="text-xs font-normal">all categories</span>
            </span>
          );
    return helper.columns([
      ...(ranked
        ? [
            helper.accessor((row) => row.standing?.best_rank, {
              id: 'rank',
              header: terms.rank.term,
              sortDescFirst: false,
              cell: ({ row: { original } }) =>
                original.standing
                  ? formatRank(original.standing.best_rank, original.standing.worst_rank)
                  : MISSING,
              meta: { numeric: true, term: terms.rank },
            }),
          ]
        : []),
      helper.accessor('name', {
        header: 'System',
        sortFn: 'alphanumeric',
        cell: ({ row: { original } }) => (
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            {original.standing ? (
              <a
                href={href(original.system)}
                className="font-medium text-balance sm:whitespace-nowrap"
              >
                {original.name}
              </a>
            ) : (
              <span className="font-medium text-balance sm:whitespace-nowrap">{original.name}</span>
            )}
            {original.system === anchorSystem && <StatusMark status="anchor" term={anchorTerm} />}
            {original.preliminary && <StatusMark status="preliminary" />}
          </span>
        ),
        meta: { wraps: true },
      }),
      helper.display({
        id: 'interval',
        header: () => <ForestAxis domain={domain} />,
        cell: ({ row }) => <IntervalCell row={row.original} domain={domain} anchor={anchor} />,
        meta: { forest: true },
      }),
      helper.accessor('score', {
        header: 'Score',
        sortDescFirst: true,
        cell: ({ row }) => (
          <span className="flex flex-col items-end leading-tight">
            <Hint term={rowDetails(row.original, ranked, gateTerm)}>
              <span className="font-medium">{formatScore(row.original.score)}</span>
            </Hint>
            <span className="text-xs text-muted-foreground">
              {formatInterval(row.original.interval)}
            </span>
          </span>
        ),
        meta: { numeric: true },
      }),
      helper.accessor('votes', {
        header: terms.votes.term,
        sortDescFirst: true,
        meta: { numeric: true, term: terms.votes, detail: true },
      }),
      helper.accessor((row) => row.standing?.both_bad_rate ?? undefined, {
        id: 'both-bad',
        header: familyWide(terms.bothBad.term),
        sortDescFirst: false,
        sortUndefined: 'last',
        cell: ({ getValue }) => formatPercent(getValue() ?? null),
        meta: { numeric: true, term: terms.bothBad, detail: true },
      }),
      helper.accessor((row) => row.standing?.gate_failure_rate ?? undefined, {
        id: 'gate-failures',
        header: familyWide(gateTerm.term),
        sortDescFirst: false,
        sortUndefined: 'last',
        cell: ({ getValue }) => formatPercent(getValue() ?? null),
        meta: { numeric: true, term: gateTerm, detail: true },
      }),
    ]);
  }, [ranked, domain, anchor, anchorSystem, anchorTerm, href, gateTerm]);

  const table = useTable({
    features,
    columns,
    data: rows,
    initialState: { sorting: [{ id: 'score', desc: true }] },
  });

  return (
    <Table>
      <TableHeader>
        {table.getHeaderGroups().map((group) => (
          <TableRow key={group.id} className="hover:bg-transparent">
            {group.headers.map((header) => {
              const { numeric, forest, term, detail } = header.column.columnDef.meta ?? {};
              if (forest) {
                return (
                  <TableHead key={header.id} className="w-[38%] p-0 align-bottom max-lg:hidden">
                    <span className="sr-only">95% interval</span>
                    <table.FlexRender header={header} />
                  </TableHead>
                );
              }
              const sorted = header.column.getIsSorted();
              return (
                <TableHead
                  key={header.id}
                  numeric={numeric}
                  className={cn(detail && 'max-md:hidden')}
                  aria-sort={
                    sorted === 'asc' ? 'ascending' : sorted === 'desc' ? 'descending' : undefined
                  }
                >
                  <span className="inline-flex items-center">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={header.column.getToggleSortingHandler()}
                      className={cn(
                        '-mx-2 px-2 text-muted-foreground',
                        numeric && 'flex-row-reverse',
                      )}
                    >
                      <table.FlexRender header={header} />
                      <SortIcon direction={sorted} />
                    </Button>
                    {term && (
                      <Hint term={term}>
                        <CircleHelp className="ml-1.5 size-3.5 text-faint" aria-hidden="true" />
                        <span className="sr-only">What {term.term} means</span>
                      </Hint>
                    )}
                  </span>
                </TableHead>
              );
            })}
          </TableRow>
        ))}
      </TableHeader>
      <TableBody>
        {table.getRowModel().rows.map((row) => (
          <TableRow key={row.id} className="h-12">
            {row.getAllCells().map((cell) => {
              const { numeric, forest, wraps, detail } = cell.column.columnDef.meta ?? {};
              return (
                <TableCell
                  key={cell.id}
                  numeric={numeric}
                  className={cn(
                    forest && 'p-0 max-lg:hidden',
                    wraps && 'whitespace-normal',
                    detail && 'max-md:hidden',
                  )}
                >
                  <table.FlexRender cell={cell} />
                </TableCell>
              );
            })}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
