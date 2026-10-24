import {
  EMPTY_DOMAIN,
  forestDomain,
  roundRows,
  unmeasuredRows,
  type ForestRow,
} from '@/lib/forest';
import { configOf, latest, systemPath, type Family } from '@/lib/reports';
import { reportNames } from '@/lib/systems';

/** The rows and axis of a voted family's home panel: its latest round, or its systems before one. */
export function panelForest(family: Family): {
  rows: ForestRow[];
  domain: [number, number];
  anchorScore?: number;
} {
  const config = configOf(family, 'votes');
  const report = latest(family);
  const results = report?.results;

  if (!report || results?.scorer !== 'votes') {
    return {
      rows: unmeasuredRows(config.systems, config.reference, family.systemNames),
      domain: EMPTY_DOMAIN,
    };
  }

  return {
    rows: roundRows(
      (system) => systemPath(family, system),
      results,
      reportNames(report.systems),
      results.rules,
    ),
    domain: forestDomain(results.standings),
    anchorScore: results.method.anchor_score,
  };
}
