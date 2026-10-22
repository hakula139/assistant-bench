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

import { PlotFigure, type PlotFrame } from '@/components/charts/PlotFigure';
import { forestMarks } from '@/components/charts/forest-marks';
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
  MISSING,
  formatCost,
  formatInterval,
  formatPercent,
  formatRank,
  formatScore,
  formatSeconds,
} from '@/lib/format';
import { clip, forestDomain, forestTicks, labelledTicks } from '@/lib/forest';
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
  /** The reference system's score, drawn as the axis baseline when the board holds it. */
  reference: number | undefined;
  /** Links a system name to its page, which only systems with a family standing have. */
  href: (system: string) => string;
}

const ROW_HEIGHT = 48;
const FOREST_MARGIN = 22;

const features = tableFeatures({
  columnMeta: metaHelper<{ numeric?: boolean; forest?: boolean; wraps?: boolean }>(),
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
      height: 28,
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
      color: { type: 'identity' },
      marks: [
        Plot.gridX(forestTicks(domain), { stroke: 'var(--chart-grid)', strokeOpacity: 1 }),
        reference === undefined
          ? null
          : Plot.ruleX([reference], { stroke: 'var(--faint)', strokeOpacity: 0.6 }),
        forestMarks(
          [{ y: 0.5, score: row.score, clipped: clip(row, domain), preliminary: row.preliminary }],
          domain,
        ),
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
          aria-label={`${row.name}: ${formatScore(row.score)}, 95% interval ${formatInterval(row.interval)}`}
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
  const domain = useMemo(() => forestDomain(rows), [rows]);

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
              header: 'Rank',
              sortDescFirst: false,
              cell: ({ row: { original } }) =>
                original.standing
                  ? formatRank(original.standing.best_rank, original.standing.worst_rank)
                  : MISSING,
              meta: { numeric: true },
            }),
          ]
        : []),
      helper.accessor('name', {
        header: 'System',
        sortFn: 'alphanumeric',
        cell: ({ row: { original } }) => (
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            {original.standing ? (
              <a href={href(original.system)} className="font-medium whitespace-nowrap">
                {original.name}
              </a>
            ) : (
              <span className="font-medium whitespace-nowrap">{original.name}</span>
            )}
            {original.preliminary && <Badge variant="status">Preliminary</Badge>}
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
      helper.accessor((row) => row.standing?.both_bad_rate ?? undefined, {
        id: 'both-bad',
        header: familyWide('Both bad'),
        sortDescFirst: false,
        sortUndefined: 'last',
        cell: ({ getValue }) => formatPercent(getValue() ?? null),
        meta: { numeric: true },
      }),
      helper.accessor((row) => row.standing?.gate_failure_rate ?? undefined, {
        id: 'gate-failures',
        header: familyWide('Gate failures'),
        sortDescFirst: false,
        sortUndefined: 'last',
        cell: ({ getValue }) => formatPercent(getValue() ?? null),
        meta: { numeric: true },
      }),
      helper.accessor((row) => row.standing?.mean_cost_usd ?? undefined, {
        id: 'cost',
        header: familyWide('Cost / output'),
        sortDescFirst: false,
        sortUndefined: 'last',
        cell: ({ getValue }) => formatCost(getValue() ?? null),
        meta: { numeric: true },
      }),
      helper.accessor((row) => row.standing?.p50_latency_sec ?? undefined, {
        id: 'latency',
        header: familyWide('p50 latency'),
        sortDescFirst: false,
        sortUndefined: 'last',
        cell: ({ getValue }) => formatSeconds(getValue() ?? null),
        meta: { numeric: true },
      }),
    ]);
  }, [ranked, domain, reference, href]);

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
                    className="w-[38%] min-w-48 p-0 align-bottom sm:min-w-72"
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
                  numeric={numeric}
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
          <TableRow key={row.id} className="group/row h-12">
            {row.getAllCells().map((cell) => {
              const { numeric, forest, wraps } = cell.column.columnDef.meta ?? {};
              return (
                <TableCell
                  key={cell.id}
                  numeric={numeric}
                  className={cn(forest && 'p-0', wraps && 'whitespace-normal')}
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
