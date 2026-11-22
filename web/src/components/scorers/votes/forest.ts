import {
  EMPTY_DOMAIN,
  forestDomain,
  roundRows,
  unmeasuredRows,
  type ForestRow,
} from '@/lib/forest';
import { displayVoteScores, fixedAnchor, NET_DOMAIN } from '@/lib/ranking';
import { systemPath, type VotesFamily } from '@/lib/reports';
import { reportNames } from '@/lib/systems';

/** A voted family's home panel rows and axis: its latest round, or its systems before one. */
export function panelForest(family: VotesFamily): {
  rows: ForestRow[];
  domain: [number, number];
  anchorScore?: number;
} {
  const report = family.rounds.at(-1);
  if (!report) {
    return {
      rows: unmeasuredRows(family.config.systems, family.systemNames),
      domain: EMPTY_DOMAIN,
    };
  }

  const results = displayVoteScores(report.results);
  return {
    rows: roundRows(
      (system) => systemPath(family, system),
      results,
      reportNames(report.systems),
      results.rules,
    ),
    domain: 'kind' in results.method ? NET_DOMAIN : forestDomain(results.standings),
    anchorScore: fixedAnchor(results.method),
  };
}
