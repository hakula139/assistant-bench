import {
  createExecutionContext,
  createScheduledController,
  waitOnExecutionContext,
} from 'cloudflare:test';
import { env } from 'cloudflare:workers';
import { expect, test } from 'vitest';

import worker from './index';
import { PAIR_RETENTION_MS, TAG_RETENTION_MS } from './limits';
import { admitted, pairOf, seedVote, WRITING } from './test/support';

const FIRST = '32be94015839ac0131d6874ffcc11c79f78e8059790f678d80fa7008a1286c22';

test('the daily cron clears IP tags after 60 days and pending pairs idle for 30', async () => {
  const now = Date.now();
  await seedVote(WRITING, FIRST, ['acme-large', 'acme-small'], {
    actor: 'old',
    ipTag: 'old-tag',
    recordedAt: now - TAG_RETENTION_MS - 60_000,
  });
  await seedVote(WRITING, FIRST, ['acme-large', 'acme-pro'], {
    actor: 'recent',
    ipTag: 'recent-tag',
    recordedAt: now - TAG_RETENTION_MS + 60_000,
  });
  await env.DB.batch([
    env.DB.prepare("UPDATE actors SET ip_tag = 'old-tag', created_at = ? WHERE id = 'old'").bind(
      now - TAG_RETENTION_MS - 60_000,
    ),
    env.DB.prepare(
      "UPDATE actors SET ip_tag = 'recent-tag', created_at = ? WHERE id = 'recent'",
    ).bind(now - TAG_RETENTION_MS + 60_000),
  ]);
  const idle = (await pairOf(await admitted('192.0.2.1'))).pair?.id;
  const active = (await pairOf(await admitted('192.0.2.2'))).pair?.id;
  await env.DB.prepare('UPDATE pairs SET issued_at = ? WHERE id = ?')
    .bind(now - PAIR_RETENTION_MS - 60_000, idle)
    .run();

  const ctx = createExecutionContext();
  // The tests run the local environment, whose bindings the merged test `env` holds.
  worker.scheduled(
    createScheduledController({ scheduledTime: now }),
    env as Cloudflare.LocalEnv,
    ctx,
  );
  await waitOnExecutionContext(ctx);

  const tags = await env.DB.prepare(
    `SELECT 'vote' AS kind, actor_id AS id, ip_tag FROM votes
     UNION ALL SELECT 'actor', id, ip_tag FROM actors WHERE id IN ('old', 'recent')
     ORDER BY kind, id`,
  ).all();
  expect(tags.results).toEqual([
    { kind: 'actor', id: 'old', ip_tag: null },
    { kind: 'actor', id: 'recent', ip_tag: 'recent-tag' },
    { kind: 'vote', id: 'old', ip_tag: null },
    { kind: 'vote', id: 'recent', ip_tag: 'recent-tag' },
  ]);
  const pairs = await env.DB.prepare('SELECT id FROM pairs').all<{ id: string }>();
  expect(pairs.results.map(({ id }) => id)).toEqual([active]);
});
