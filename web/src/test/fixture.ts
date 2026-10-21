import { readFile } from 'node:fs/promises';

/**
 * Reads a committed report fixture as raw JSON, so each test can alter its own copy: `report`
 * after ten simulated sessions, or `report-early` after the first, which has no decision. The
 * pydantic models write both, and a pytest fails when either drifts from them.
 */
export async function readFixture(
  name: 'report' | 'report-early' = 'report',
): Promise<Record<string, unknown>> {
  const path = new URL(`../../../tests/fixtures/${name}.json`, import.meta.url);
  return JSON.parse(await readFile(path, 'utf8')) as Record<string, unknown>;
}
