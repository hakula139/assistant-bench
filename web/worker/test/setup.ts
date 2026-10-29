import { applyD1Migrations } from 'cloudflare:test';
import { env } from 'cloudflare:workers';
import { beforeEach } from 'vitest';

await applyD1Migrations(env.DB, env.TEST_MIGRATIONS);

// Each statement of the dump starts a line, while string values may span several.
const publication = env.TEST_PUBLICATION.split(/;\n(?=INSERT|$)/).filter(
  (statement) => statement.trim() !== '',
);

beforeEach(async () => {
  await env.DB.batch([
    ...['votes', 'pairs', 'actors', 'active', 'outputs', 'cases', 'publications'].map((table) =>
      env.DB.prepare(`DELETE FROM ${table}`),
    ),
    ...publication.map((statement) => env.DB.prepare(statement)),
  ]);
});
