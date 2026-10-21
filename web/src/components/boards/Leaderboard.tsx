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
import { ArrowDown, ArrowUp } from 'lucide-react';
import { useCallback, useMemo } from 'react';

import { PlotFigure } from '@/components/charts/PlotFigure';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import {
  formatCost,
  formatInterval,
  formatPercent,
  formatRank,
  formatScore,
  formatSeconds,
} from '@/lib/format';
import type { Interval, Standing } from '@/lib/report';

/**
 * A system's row, whose score fields come from the board in view: the family's, or a category's
 * refit, which carries no rank spread.
 */
export interface LeaderboardRow {
  standing: Standing;
  score: number;
  interval: Interval;
  votes: number;
  preliminary: boolean;
}

interface LeaderboardProps {
  rows: LeaderboardRow[];
  /** Whether the rows carry the family board's ranks, which category refits lack. */
  ranked: boolean;
  /** The reference system's score, drawn as the axis baseline when the board holds it. */
  reference: number | undefined;
  /** Links each system name to its page. */
  href: (system: string) => string;
}

const ROW_HEIGHT = 44;
const FOREST_MARGIN = 22;

const features = tableFeatures({
  columnMeta: metaHelper<{ numeric?: boolean; forest?: boolean; wraps?: boolean }>(),
  rowSortingFeature,
  sortedRowModel: createSortedRowModel(),
  sortFns: { alphanumeric: sortFn_alphanumeric, basic: sortFn_basic },
});
const helper = createColumnHelper<typeof features, LeaderboardRow>();

function scoreDomain(rows: LeaderboardRow[]): [number, number] {
  const lows = rows.map((row) => row.interval.lower);
  const highs = rows.map((row) => row.interval.upper);
  return [Math.min(...lows), Math.max(...highs)];
}

/** The shared x scale, so the header axis and every row's interval line up. */
function forestScale(domain: [number, number]): Plot.ScaleOptions {
  return { domain, nice: true, insetLeft: FOREST_MARGIN, insetRight: FOREST_MARGIN };
}

function ForestAxis({ domain }: { domain: [number, number] }) {
  const plot = useCallback(
    (): Plot.PlotOptions => ({
      height: 28,
      margin: 0,
      marginTop: 20,
      x: { ...forestScale(domain), axis: 'top', tickSize: 0, label: null },
      marks: [],
    }),
    [domain],
  );
  return <PlotFigure plot={plot} height={28} />;
}

function ForestRow({
  row,
  domain,
  reference,
}: {
  row: LeaderboardRow;
  domain: [number, number];
  reference: number | undefined;
}) {
  const plot = useCallback(
    (): Plot.PlotOptions => ({
      height: ROW_HEIGHT,
      margin: 0,
      x: { ...forestScale(domain), axis: null },
      y: { axis: null, domain: [0, 1] },
      marks: [
        Plot.gridX({ stroke: 'var(--chart-grid)', strokeOpacity: 1 }),
        reference === undefined
          ? null
          : Plot.ruleX([reference], { stroke: 'var(--faint)', strokeOpacity: 0.6 }),
        Plot.ruleY([row], {
          x1: (d: LeaderboardRow) => d.interval.lower,
          x2: (d: LeaderboardRow) => d.interval.upper,
          y: 0.5,
          stroke: 'var(--chart-series)',
          strokeWidth: 2,
          strokeLinecap: 'round',
          className: 'forest-mark',
        }),
        Plot.dot([row], {
          x: 'score',
          y: 0.5,
          r: 5,
          fill: row.preliminary ? 'var(--background)' : 'var(--chart-series)',
          stroke: row.preliminary ? 'var(--chart-series)' : 'var(--background)',
          strokeWidth: 2,
          className: 'forest-mark',
        }),
      ],
    }),
    [row, domain, reference],
  );

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div
          role="img"
          tabIndex={0}
          aria-label={`${row.standing.system}: ${formatScore(row.score)}, 95% interval ${formatInterval(row.interval)}`}
          className="focus-visible:outline-offset-[-2px]"
        >
          <PlotFigure plot={plot} height={ROW_HEIGHT} />
        </div>
      </TooltipTrigger>
      <TooltipContent side="top">
        <span className="font-semibold">{formatScore(row.score)}</span>
        <span>95% interval {formatInterval(row.interval)}</span>
      </TooltipContent>
    </Tooltip>
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

/** The leaderboard table, whose interval column is a forest plot on one shared axis. */
export function Leaderboard({ rows, ranked, reference, href }: LeaderboardProps) {
  const domain = useMemo(() => scoreDomain(rows), [rows]);

  const columns = useMemo(
    () =>
      helper.columns([
        ...(ranked
          ? [
              helper.accessor((row) => row.standing.best_rank, {
                id: 'rank',
                header: 'Rank',
                sortDescFirst: false,
                cell: ({ row }) =>
                  formatRank(row.original.standing.best_rank, row.original.standing.worst_rank),
                meta: { numeric: true },
              }),
            ]
          : []),
        helper.accessor((row) => row.standing.system, {
          id: 'system',
          header: 'System',
          sortFn: 'alphanumeric',
          cell: ({ row }) => (
            <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <a
                href={href(row.original.standing.system)}
                className="font-medium whitespace-nowrap"
              >
                {row.original.standing.system}
              </a>
              {row.original.preliminary && (
                <Badge variant="outline" className="font-normal text-muted-foreground">
                  Preliminary
                </Badge>
              )}
            </span>
          ),
          meta: { wraps: true },
        }),
        helper.display({
          id: 'interval',
          header: () => <ForestAxis domain={domain} />,
          cell: ({ row }) => <ForestRow row={row.original} domain={domain} reference={reference} />,
          meta: { forest: true },
        }),
        helper.accessor('score', {
          header: 'Score',
          sortDescFirst: true,
          cell: ({ row }) => (
            <span className="flex flex-col leading-tight">
              <span className="font-medium">{formatScore(row.original.score)}</span>
              <span className="text-xs text-muted-foreground">
                {formatInterval(row.original.interval)}
              </span>
            </span>
          ),
          meta: { numeric: true },
        }),
        helper.accessor('votes', {
          header: 'Votes',
          sortDescFirst: true,
          meta: { numeric: true },
        }),
        helper.accessor((row) => row.standing.both_bad_rate, {
          id: 'both-bad',
          header: 'Both bad',
          sortDescFirst: false,
          cell: ({ getValue }) => formatPercent(getValue()),
          meta: { numeric: true },
        }),
        helper.accessor((row) => row.standing.gate_failure_rate ?? undefined, {
          id: 'gate-failures',
          header: 'Gate failures',
          sortDescFirst: false,
          sortUndefined: 'last',
          cell: ({ getValue }) => formatPercent(getValue() ?? null),
          meta: { numeric: true },
        }),
        helper.accessor((row) => row.standing.mean_cost_usd ?? undefined, {
          id: 'cost',
          header: 'Cost / output',
          sortDescFirst: false,
          sortUndefined: 'last',
          cell: ({ getValue }) => formatCost(getValue() ?? null),
          meta: { numeric: true },
        }),
        helper.accessor((row) => row.standing.p50_latency_sec ?? undefined, {
          id: 'latency',
          header: 'p50 latency',
          sortDescFirst: false,
          sortUndefined: 'last',
          cell: ({ getValue }) => formatSeconds(getValue() ?? null),
          meta: { numeric: true },
        }),
      ]),
    [ranked, domain, reference, href],
  );

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
              const { numeric, forest } = header.column.columnDef.meta ?? {};
              if (forest) {
                return (
                  <TableHead
                    key={header.id}
                    className="w-[32%] min-w-40 p-0 align-bottom sm:min-w-56"
                  >
                    <span className="sr-only">95% interval</span>
                    <table.FlexRender header={header} />
                  </TableHead>
                );
              }
              const sorted = header.column.getIsSorted();
              return (
                <TableHead
                  key={header.id}
                  className={numeric ? 'text-right' : undefined}
                  aria-sort={
                    sorted === 'asc' ? 'ascending' : sorted === 'desc' ? 'descending' : undefined
                  }
                >
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={header.column.getToggleSortingHandler()}
                    className={cn('-mx-2.5 text-muted-foreground', numeric && 'flex-row-reverse')}
                  >
                    <table.FlexRender header={header} />
                    <SortIcon direction={sorted} />
                  </Button>
                </TableHead>
              );
            })}
          </TableRow>
        ))}
      </TableHeader>
      <TableBody>
        {table.getRowModel().rows.map((row) => (
          <TableRow key={row.id} className="group/row h-11">
            {row.getAllCells().map((cell) => {
              const { numeric, forest, wraps } = cell.column.columnDef.meta ?? {};
              return (
                <TableCell
                  key={cell.id}
                  className={cn(
                    forest && 'p-0',
                    numeric && 'text-right tabular-nums',
                    wraps && 'whitespace-normal',
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
