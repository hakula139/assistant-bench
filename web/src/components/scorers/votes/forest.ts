import {
  EMPTY_DOMAIN,
  forestDomain,
  roundRows,
  unmeasuredRows,
  type ForestRow,
} from '@/lib/forest';
import { configOf, latest, systemPath, type Family } from '@/lib/reports';

/** The rows and axis of a voted family's home panel: its latest round, or its systems before one. */
export function panelForest(family: Family): { rows: ForestRow[]; domain: [number, number] } {
  const config = configOf(family, 'votes');
  const results = latest(family)?.results;
  if (results?.scorer !== 'votes') {
    return {
      rows: unmeasuredRows(config.systems, config.reference, family.systemNames),
      domain: EMPTY_DOMAIN,
    };
  }
  return {
    rows: roundRows(
      (system) => systemPath(family, system),
      config.systems,
      results,
      family.systemNames,
    ),
    domain: forestDomain(results.standings),
  };
}
