import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { currentReportsSchema, loadCurrentReports, systemConfigSchema } from '@/content.config';
import { readReport } from '@/test/fixture';

describe('collection schemas', () => {
  test('keeps only the display name of a systems.toml table', () => {
    const input = { route: 'private-route', effort: 'high', display_name: 'Acme · High' };

    const parsed = systemConfigSchema.parse(input);

    expect(parsed).toEqual({ display_name: 'Acme · High' });
  });

  test('rejects a table without a display name', () => {
    expect(systemConfigSchema.safeParse({ route: 'private-route' }).success).toBe(false);
  });
});

describe('current reports', () => {
  let workspace: string;
  let output: string;

  beforeEach(async () => {
    workspace = await mkdtemp(join(tmpdir(), 'bench-arena-overlay-'));
    const arena = join(workspace, '.arena');
    await mkdir(arena);
    output = join(arena, 'current.json');
  });

  afterEach(async () => {
    await rm(workspace, { recursive: true, force: true });
  });

  function context() {
    return {
      config: { root: pathToFileURL(`${workspace}/`) },
      store: { clear: vi.fn(), set: vi.fn() },
      async parseData<T extends Record<string, unknown>>({ data }: { data: T }): Promise<T> {
        await currentReportsSchema.parseAsync(data);
        return data;
      },
    };
  }

  test('uses an empty report map without a release file', async () => {
    const input = context();

    await loadCurrentReports(input);

    expect(input.store.clear).toHaveBeenCalledOnce();
    expect(input.store.set).toHaveBeenCalledExactlyOnceWith({
      id: 'current',
      data: { reports: {} },
    });
  });

  test('loads validated reports and explicit empty boards', async () => {
    const report = await readReport('report', 'votes');
    const reports = { [report.family]: report, 'writing-zh': null };
    await writeFile(output, JSON.stringify(reports));
    const input = context();

    await loadCurrentReports(input);

    expect(input.store.set).toHaveBeenCalledExactlyOnceWith({ id: 'current', data: { reports } });
  });

  test.each(['{', '[]', '{"writing-zh":{"results":{}}}'])(
    'rejects invalid input %s',
    async (source) => {
      await writeFile(output, source);
      const input = context();

      await expect(loadCurrentReports(input)).rejects.toThrow();
      expect(input.store.set).not.toHaveBeenCalled();
    },
  );

  test('propagates a read error other than a missing file', async () => {
    await mkdir(output);
    const input = context();

    await expect(loadCurrentReports(input)).rejects.toMatchObject({ code: 'EISDIR' });
    expect(input.store.set).not.toHaveBeenCalled();
  });
});
