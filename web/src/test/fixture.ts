import { readFile } from 'node:fs/promises';

/**
 * Reads a committed fixture as raw JSON, so each test can alter its own copy: `report` after ten
 * simulated sessions, `report-early` after the first, which has no decision, or `report.schema`.
 * The pydantic models write all three, and a pytest fails when one drifts from them.
 */
export async function readFixture(
  name: 'report' | 'report-early' | 'report.schema' = 'report',
): Promise<Record<string, unknown>> {
  const path = new URL(`../../../tests/fixtures/${name}.json`, import.meta.url);
  return JSON.parse(await readFile(path, 'utf8')) as Record<string, unknown>;
}
