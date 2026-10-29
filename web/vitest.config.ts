/// <reference types="vitest/config" />
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

import { cloudflareTest, readD1Migrations } from '@cloudflare/vitest-plugin';
import { getViteConfig } from 'astro/config';

export default getViteConfig({
  test: {
    unstubGlobals: true,
    restoreMocks: true,
    coverage: {
      enabled: true,
      // workerd exposes no V8 coverage, so both projects instrument their sources.
      provider: 'istanbul',
      include: ['src/**/*.{astro,ts,tsx}', 'worker/**/*.ts'],
      exclude: ['src/components/ui/**', 'src/test/**', 'worker/test/**'],
      // Codecov matches lcov paths against the repository, so they start at its root.
      reporter: ['text-summary', ['lcovonly', { projectRoot: '..' }]],
    },
    projects: [
      {
        extends: true,
        test: { name: 'site', include: ['src/**/*.test.{ts,tsx}'] },
      },
      {
        plugins: [
          cloudflareTest(async () => ({
            wrangler: { configPath: './wrangler.toml', environment: 'local' },
            miniflare: {
              bindings: {
                ACTOR_KEY: 'test-actor-key',
                IP_TAG_KEY: 'test-ip-tag-key',
                TEST_MIGRATIONS: await readD1Migrations('migrations'),
                TEST_PUBLICATION: await readFile(
                  new URL('../tests/fixtures/arena-publication.sql', import.meta.url),
                  'utf8',
                ),
              },
            },
          })),
        ],
        resolve: { alias: { '@': fileURLToPath(new URL('src', import.meta.url)) } },
        test: {
          name: 'worker',
          include: ['worker/**/*.test.ts'],
          setupFiles: ['worker/test/setup.ts'],
          restoreMocks: true,
        },
      },
    ],
  },
});
