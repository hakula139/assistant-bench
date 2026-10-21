import { readFile } from 'node:fs/promises';

// The pydantic models write this fixture, and a pytest fails when it drifts from them.
const FIXTURE = new URL('../../../tests/fixtures/report.json', import.meta.url);

/** Reads the committed report fixture as raw JSON, so each test can alter its own copy. */
export async function readFixture(): Promise<Record<string, unknown>> {
  return JSON.parse(await readFile(FIXTURE, 'utf8')) as Record<string, unknown>;
}
