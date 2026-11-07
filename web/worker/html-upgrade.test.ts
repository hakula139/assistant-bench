import { applyD1Migrations } from 'cloudflare:test';
import { env } from 'cloudflare:workers';
import { describe, expect, test } from 'vitest';

import { admitted, call, CODING, pairOf, seedVote, vote, WRITING } from './test/support';

const tables = [
  'votes',
  'pair_vote_counts',
  'pairs',
  'actors',
  'active',
  'publication_pair_cases',
  'outputs',
  'cases',
  'publications',
] as const;

async function storedRows(): Promise<Record<string, Record<string, unknown>[]>> {
  return Object.fromEntries(
    await Promise.all(
      tables.map(async (table) => {
        const { results } = await env.DB.prepare(`SELECT * FROM ${table} ORDER BY rowid`).all();
        return [table, results] as const;
      }),
    ),
  );
}

async function indexes() {
  return (
    await env.DB.prepare(
      "SELECT name, sql FROM sqlite_master WHERE type = 'index' ORDER BY name",
    ).all()
  ).results;
}

async function expectCountedVotes() {
  const stored = await env.DB.prepare(
    `SELECT family, low_system, high_system, votes FROM pair_vote_counts
     WHERE votes > 0 ORDER BY family, low_system, high_system`,
  ).all();
  const counted = await env.DB.prepare(
    `SELECT family, min(a_system, b_system) AS low_system,
       max(a_system, b_system) AS high_system, count(*) AS votes
     FROM votes WHERE exclusion IS NULL
     GROUP BY family, low_system, high_system ORDER BY family, low_system, high_system`,
  ).all();
  expect(stored.results).toEqual(counted.results);
}

describe('0005_html_outputs', () => {
  test('upgrades populated text and diff data while preserving votes, pending pairs and triggers', async () => {
    for (const table of tables) {
      await env.DB.prepare(`DROP TABLE ${table}`).run();
    }
    const migration = env.TEST_MIGRATIONS.find(({ name }) => name === '0005_html_outputs.sql');
    if (migration === undefined) {
      throw new Error('Expected the HTML output migration.');
    }
    await applyD1Migrations(
      env.DB,
      env.TEST_MIGRATIONS.filter(({ name }) => name < migration.name),
      'upgrade_migrations',
    );
    const fixture = env.TEST_PUBLICATION.split(/;\n(?=INSERT|$)/).filter(
      (statement) => statement.trim() !== '',
    );
    await env.DB.batch(fixture.map((statement) => env.DB.prepare(statement)));
    await expect(
      env.DB.prepare("UPDATE publications SET render = 'html' WHERE family = ?")
        .bind(WRITING)
        .run(),
    ).rejects.toThrow();
    await expect(env.DB.prepare('UPDATE outputs SET length = NULL').run()).rejects.toThrow();
    const caseKey = await env.DB.prepare(
      `SELECT a.case_key FROM outputs AS a JOIN outputs AS b
       ON b.publication_id = a.publication_id AND b.case_key = a.case_key
       JOIN active ON active.publication_id = a.publication_id
       WHERE active.family = ? AND a.system = 'acme-large' AND b.system = 'acme-small'
         AND a.content_digest <> b.content_digest LIMIT 1`,
    )
      .bind(WRITING)
      .first<string>('case_key');
    if (caseKey === null) {
      throw new Error('Expected a pairable legacy case.');
    }
    await seedVote(WRITING, caseKey, ['acme-large', 'acme-small'], { actor: 'counted-reader' });
    await seedVote(WRITING, caseKey, ['acme-large', 'acme-small'], {
      actor: 'excluded-reader',
      exclusion: 'spam',
    });
    const textActor = await admitted('192.0.2.11');
    const diffActor = await admitted('192.0.2.12');
    const text = await pairOf(textActor, WRITING);
    const diff = await pairOf(diffActor, CODING);
    expect((await vote(textActor, text.pair?.id ?? '')).status).toBe(204);
    await pairOf(textActor, WRITING);
    const before = await storedRows();
    const oldIndexes = await indexes();
    await expectCountedVotes();

    await applyD1Migrations(env.DB, [migration], 'upgrade_migrations');

    expect(await storedRows()).toEqual(before);
    expect(await indexes()).toEqual(oldIndexes);
    expect((await env.DB.prepare('PRAGMA foreign_key_check').all()).results).toEqual([]);
    expect(await env.DB.prepare('PRAGMA quick_check').first('quick_check')).toBe('ok');
    expect(await pairOf(diffActor, CODING)).toEqual(diff);
    expect((await call(textActor, 'POST', '/votes/undo', { family: WRITING })).status).toBe(204);
    expect((await pairOf(textActor, WRITING)).pair).toEqual(text.pair);
    expect((await vote(textActor, text.pair?.id ?? '')).status).toBe(204);
    expect((await vote(diffActor, diff.pair?.id ?? '')).status).toBe(204);
    expect((await call(diffActor, 'POST', '/votes/undo', { family: CODING })).status).toBe(204);
    expect((await pairOf(diffActor, CODING)).pair).toEqual(diff.pair);
    await expectCountedVotes();

    for (const exclusion of ['spam', null]) {
      await env.DB.prepare('UPDATE votes SET exclusion = ? WHERE actor_id = ?')
        .bind(exclusion, 'counted-reader')
        .run();
      await expectCountedVotes();
    }
    const original = await env.DB.prepare('SELECT * FROM votes WHERE actor_id = ?')
      .bind('counted-reader')
      .first<Record<string, string | number | null>>();
    if (original === null) {
      throw new Error('Expected the legacy counted vote.');
    }
    await env.DB.prepare('DELETE FROM votes WHERE actor_id = ?').bind('counted-reader').run();
    await expectCountedVotes();
    const columns = Object.keys(original);
    await env.DB.prepare(
      `INSERT INTO votes (${columns.join(', ')}) VALUES (${columns.map(() => '?').join(', ')})`,
    )
      .bind(...Object.values(original))
      .run();
    await expectCountedVotes();
    await env.DB.prepare('DELETE FROM votes WHERE actor_id = ?').bind('excluded-reader').run();
    await expectCountedVotes();

    await env.DB.prepare("UPDATE publications SET render = 'html' WHERE family = ?")
      .bind(WRITING)
      .run();
    await env.DB.prepare(
      'UPDATE outputs SET length = NULL WHERE publication_id IN (SELECT id FROM publications WHERE family = ?)',
    )
      .bind(WRITING)
      .run();
    expect(
      await env.DB.prepare('SELECT render FROM publications WHERE family = ?')
        .bind(WRITING)
        .first('render'),
    ).toBe('html');
    expect(
      await env.DB.prepare(
        'SELECT length FROM outputs WHERE publication_id IN (SELECT id FROM publications WHERE family = ?) LIMIT 1',
      )
        .bind(WRITING)
        .first('length'),
    ).toBeNull();
    await expect(
      env.DB.prepare("UPDATE publications SET render = 'unknown' WHERE family = ?")
        .bind(WRITING)
        .run(),
    ).rejects.toThrow();
    expect((await env.DB.prepare('PRAGMA foreign_key_check').all()).results).toEqual([]);
  });
});
