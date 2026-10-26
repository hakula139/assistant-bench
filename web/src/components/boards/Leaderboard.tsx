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
import { useMemo } from 'react';

import { ScoreTrack, TrackAxis } from '@/components/site/ScoreTrack';
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
import { forestDomain, legendMarks, onAxis, type LegendMark } from '@/lib/forest';
import { formatCount, formatCountOf, formatPercent, formatScore, MISSING } from '@/lib/format';
import { terms, type Term } from '@/lib/glossary';
import type { Standing } from '@/lib/report';

/** Score fields belong to the selected overall or category fit. */
export interface LeaderboardRow {
  system: string;
  /** The system's display name, which the row shows and sorts by. */
  name: string;
  /** The system's page, which only systems with a family standing have. */
  href?: string;
  /**
   * The system's standing on the family board, which also holds its family-wide rates, cost, and
   * latency. A category can rank a system that the family board leaves unranked.
   */
  standing: Standing | undefined;
  score: number;
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
  preliminaryTerm: Term;
  /** The gate-failure column's label and meaning. */
  gateFailures: Term;
}

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

function ScoreCell({
  row,
  domain,
  anchor,
}: {
  row: LeaderboardRow;
  domain: [number, number];
  anchor: number | undefined;
}) {
  const summary = `${row.name}: ${formatScore(row.score)}`;
  return (
    <div role="img" aria-label={summary} title={summary} className="absolute inset-0">
      <ScoreTrack domain={domain} anchorScore={anchor} estimate={row} />
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
function rowDetails(row: LeaderboardRow, ranked: boolean, gateFailures: Term): Term {
  const rates = `${terms.bothBad.term.toLowerCase()} ${formatPercent(row.standing?.both_bad_rate ?? null)}, ${gateFailures.term.toLowerCase()} ${formatPercent(row.standing?.gate_failure_rate ?? null)}`;
  return {
    term: row.name,
    definition: `${formatCountOf(row.votes, 'vote')}, ${rates}${ranked ? '' : ' across all categories'}.`,
  };
}

const anchorScore = (rows: LeaderboardRow[], anchorSystem: string) =>
  rows.find((row) => row.system === anchorSystem)?.score;

/** Returns the marks a `Leaderboard` of `rows` draws, for its legend. */
export function leaderboardMarks(rows: LeaderboardRow[], anchorSystem: string): LegendMark[] {
  const domain = forestDomain(rows);
  const anchor = anchorScore(rows, anchorSystem);

  return legendMarks(
    rows.map((row) => ({
      system: row.system,
      name: row.name,
      estimate: row,
      preliminary: row.preliminary,
    })),
    domain,
    anchor !== undefined && onAxis(domain, anchor),
  );
}

/** Point scores share one axis. Narrow views keep the same values in the score column. */
export function Leaderboard({
  rows,
  ranked,
  anchorSystem,
  anchorTerm,
  preliminaryTerm,
  gateFailures,
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
            helper.accessor((row) => row.standing?.rank, {
              id: 'rank',
              header: terms.rank.term,
              sortDescFirst: false,
              cell: ({ row: { original } }) =>
                original.standing ? original.standing.rank : MISSING,
              meta: { numeric: true, term: terms.rank },
            }),
          ]
        : []),
      helper.accessor('name', {
        header: 'System',
        sortFn: 'alphanumeric',
        cell: ({ row: { original } }) => (
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            {original.href ? (
              <a href={original.href} className="font-medium text-balance sm:whitespace-nowrap">
                {original.name}
              </a>
            ) : (
              <span className="font-medium text-balance sm:whitespace-nowrap">{original.name}</span>
            )}
            {original.system === anchorSystem && <StatusMark status="anchor" term={anchorTerm} />}
            {original.preliminary && <StatusMark status="preliminary" term={preliminaryTerm} />}
          </span>
        ),
        meta: { wraps: true },
      }),
      helper.display({
        id: 'point',
        header: () => <TrackAxis domain={domain} anchorScore={anchor} />,
        cell: ({ row }) => <ScoreCell row={row.original} domain={domain} anchor={anchor} />,
        meta: { forest: true },
      }),
      helper.accessor('score', {
        header: 'Score',
        sortDescFirst: true,
        cell: ({ row }) => (
          <span className="flex flex-col items-end leading-tight">
            <Hint term={rowDetails(row.original, ranked, gateFailures)}>
              <span className="font-medium">{formatScore(row.original.score)}</span>
            </Hint>
          </span>
        ),
        meta: { numeric: true },
      }),
      helper.accessor('votes', {
        header: terms.votes.term,
        sortDescFirst: true,
        cell: ({ getValue }) => formatCount(getValue()),
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
        header: familyWide(gateFailures.term),
        sortDescFirst: false,
        sortUndefined: 'last',
        cell: ({ getValue }) => formatPercent(getValue() ?? null),
        meta: { numeric: true, term: gateFailures, detail: true },
      }),
    ]);
  }, [ranked, domain, anchor, anchorSystem, anchorTerm, preliminaryTerm, gateFailures]);

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
                    <span className="sr-only">Point score</span>
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
                    forest && 'relative p-0 max-lg:hidden',
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
