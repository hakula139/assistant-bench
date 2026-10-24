import type { ForestPlotRow } from '@/components/charts/ForestPlot';
import { Stat } from '@/components/site/PageParts';
import { Hint, StatusMark } from '@/components/site/Term';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { formatCount, formatPercent, formatScore, formatSeconds, MISSING } from '@/lib/format';
import {
  defectTerms,
  fallbackAnchor,
  fingerprintTerm,
  preliminaryTerm,
  terms,
} from '@/lib/glossary';
import { roundMethod } from '@/lib/method';
import { isPreliminary } from '@/lib/ranking';
import type {
  Defect,
  Distribution,
  HeadToHead,
  PublicSystem,
  Standing,
  VotesMethod,
  VotesRules,
} from '@/lib/report';
import { reportNames, systemName, type SystemNames } from '@/lib/systems';

/**
 * The system page's Votes figure as one island, so the decisive count's hint is the element a tap
 * lands on, which an Astro slot around it would not be.
 */
export function VotesStat({ votes, decisive }: { votes: number; decisive: number }) {
  return (
    <Stat
      label="Votes"
      detail={<Hint term={terms.decisive}>({formatCount(decisive)} decisive)</Hint>}
    >
      {formatCount(votes)}
    </Stat>
  );
}

export function HeadToHeadTable({
  headToHead,
  names,
}: {
  headToHead: HeadToHead[];
  names: SystemNames;
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Opponent</TableHead>
          <TableHead numeric>Votes</TableHead>
          <TableHead numeric>Wins</TableHead>
          <TableHead numeric>Losses</TableHead>
          <TableHead numeric>Ties</TableHead>
          <TableHead numeric>Both bad</TableHead>
          <TableHead numeric>Win rate</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {headToHead.map((pair) => (
          <TableRow key={pair.opponent}>
            <TableCell>{systemName(names, pair.opponent)}</TableCell>
            <TableCell numeric>{pair.votes}</TableCell>
            <TableCell numeric>{pair.wins}</TableCell>
            <TableCell numeric>{pair.losses}</TableCell>
            <TableCell numeric>{pair.ties}</TableCell>
            <TableCell numeric>{pair.both_bad}</TableCell>
            <TableCell numeric>{formatPercent(pair.win_rate)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export function CategoryTable({
  categories,
  rules,
}: {
  categories: ForestPlotRow[];
  rules: VotesRules;
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Category</TableHead>
          <TableHead numeric>Score</TableHead>
          <TableHead numeric>Votes</TableHead>
          <TableHead>Coverage</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {categories.map(({ label, refit }) => (
          <TableRow key={label}>
            <TableCell>{label}</TableCell>
            <TableCell numeric>{formatScore(refit.score)}</TableCell>
            <TableCell numeric>{refit.votes}</TableCell>
            <TableCell>{isPreliminary(refit, rules) ? 'preliminary' : ''}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function formatDistribution(distribution: Distribution | null, format: (value: number) => string) {
  return distribution === null
    ? MISSING
    : `${format(distribution.p50)} (${format(distribution.p10)}–${format(distribution.p90)})`;
}

/** Lists every system's output tokens and latency as the median and the p10 to p90 range. */
export function DistributionTable({
  standings,
  system,
  names,
}: {
  standings: Standing[];
  system: string;
  names: SystemNames;
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>System</TableHead>
          <TableHead numeric>Output tokens</TableHead>
          <TableHead numeric>Latency</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {standings.map((item) => (
          <TableRow key={item.system} className={item.system === system ? 'font-medium' : ''}>
            <TableCell>{systemName(names, item.system)}</TableCell>
            <TableCell numeric>{formatDistribution(item.output_tokens, formatCount)}</TableCell>
            <TableCell numeric>{formatDistribution(item.latency_sec, formatSeconds)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** Counts each defect the standing's family lists, in the family's order, as the report keys them. */
export function DefectsTable({ standing }: { standing: Standing }) {
  const tagged = Object.entries(standing.defects) as [Defect, number][];

  return (
    <Table className="max-w-xl">
      <TableHeader>
        <TableRow>
          <TableHead>Issue</TableHead>
          <TableHead numeric>Votes</TableHead>
          <TableHead numeric>Share</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {tagged.map(([defect, count]) => (
          <TableRow key={defect}>
            <TableCell>
              <Hint term={defectTerms[defect]} />
            </TableCell>
            <TableCell numeric>{count}</TableCell>
            <TableCell numeric>
              {formatPercent(standing.votes === 0 ? null : count / standing.votes)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** Lists the system's standing in each round it appeared in, given oldest first. */
export function RoundsTable({
  appearances,
}: {
  appearances: {
    round: string;
    reference: string;
    anchor: string;
    systems: Record<string, PublicSystem>;
    method: VotesMethod;
    rules: VotesRules;
    standing: Standing;
  }[];
}) {
  return (
    <Table className="max-w-xl text-xs sm:text-sm">
      <TableHeader>
        <TableRow>
          <TableHead>Round</TableHead>
          <TableHead numeric>Score</TableHead>
          <TableHead numeric>Votes</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {appearances
          .toReversed()
          .map(({ round, reference, anchor, systems, method, rules, standing: past }) => {
            const names = reportNames(systems);
            const identity = systems[past.system];

            return (
              <TableRow key={round}>
                <TableCell>
                  <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    {identity.fingerprint === null ? (
                      <span title={identity.display_name}>{round}</span>
                    ) : (
                      <Hint
                        term={{
                          ...fingerprintTerm(identity.fingerprint),
                          term: identity.display_name,
                        }}
                      >
                        {round}
                      </Hint>
                    )}
                    {anchor !== reference && (
                      <StatusMark
                        status="anchor"
                        term={fallbackAnchor(
                          systemName(names, anchor),
                          systemName(names, reference),
                          method,
                        )}
                      />
                    )}
                    {isPreliminary(past, rules) && (
                      <StatusMark status="preliminary" term={preliminaryTerm(rules)} />
                    )}
                  </span>
                </TableCell>
                <TableCell numeric>
                  <Hint
                    term={{
                      term: 'Round settings',
                      definition: `${identity.display_name}. Reference: ${systemName(names, reference)}. Anchor: ${systemName(names, anchor)}. ${roundMethod(method, rules)}`,
                    }}
                  >
                    {formatScore(past.score)}
                  </Hint>
                </TableCell>
                <TableCell numeric>{past.votes}</TableCell>
              </TableRow>
            );
          })}
      </TableBody>
    </Table>
  );
}
