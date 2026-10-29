import { sValidator } from '@hono/standard-validator';
import { Hono } from 'hono';
import { bodyLimit } from 'hono/body-limit';
import { HTTPException } from 'hono/http-exception';
import { routePath } from 'hono/route';

import {
  admissionRequest,
  familyQuery,
  freshnessQuery,
  skipRequest,
  undoRequest,
  voteRequest,
  type ErrorView,
} from '@/lib/arena';

import {
  admit,
  claimedActor,
  ipTag,
  issueCookie,
  knownActor,
  requireActor,
  verifyChallenge,
} from './actors';
import { ApiError, type Api, type Bindings } from './api';
import { BODY_LIMIT_BYTES } from './limits';
import { pairState, skip } from './pairs';
import { retain } from './retention';
import { cachedFreshness, freshness, record, undo } from './votes';

/** Answers every request that fails its schema with 400 `invalid_request`. */
function rejectInvalid(result: { success: boolean }) {
  if (!result.success) {
    throw new ApiError(400, 'invalid_request');
  }
}

/** The address Cloudflare saw, which it and Miniflare set on every request. */
function address(request: Request): string {
  const value = request.headers.get('CF-Connecting-IP');
  if (value === null) {
    throw new Error('The request has no CF-Connecting-IP header.');
  }
  return value;
}

export const app = new Hono<Api>()
  .basePath('/api')
  .use(async (c, next) => {
    await next();
    c.header('Cache-Control', 'no-store');
  })
  .use(async (c, next) => {
    if (c.req.method === 'POST' && c.req.header('Origin') !== c.env.APP_ORIGIN) {
      throw new ApiError(403, 'forbidden');
    }
    await next();
  })
  .use(
    bodyLimit({
      maxSize: BODY_LIMIT_BYTES,
      onError: () => {
        throw new ApiError(413, 'too_large');
      },
    }),
  )
  .use(async (c, next) => {
    c.set('tag', await ipTag(c.env.IP_TAG_KEY, address(c.req.raw)));
    c.set('claimed', await claimedActor(c));
    const { success } = await c.env.RATE_LIMIT.limit({ key: c.get('claimed') ?? c.get('tag') });
    if (!success) {
      throw new ApiError(429, 'rate_limited');
    }
    await next();
  })
  .get('/families', async (c) => {
    const { results } = await c.env.DB.prepare(
      `SELECT active.family, publications.display_name, publications.aspect
       FROM active JOIN publications ON publications.id = active.publication_id
       ORDER BY active.family`,
    ).all<{ family: string; display_name: string; aspect: string | null }>();
    return c.json({ families: results });
  })
  .post('/actors', sValidator('json', admissionRequest, rejectInvalid), async (c) => {
    const now = Date.now();
    if (!(await knownActor(c.env.DB, c.get('claimed'), now))) {
      await verifyChallenge(c.env, c.req.valid('json').token, address(c.req.raw));
      await issueCookie(c, await admit(c.env.DB, c.get('tag'), now));
    }
    return c.body(null, 204);
  })
  .get('/pair', sValidator('query', familyQuery, rejectInvalid), async (c) => {
    const now = Date.now();
    const actor = await requireActor(c, now);
    return c.json(
      await pairState(c.env.DB, c.env.ACTOR_KEY, actor, c.req.valid('query').family, now),
    );
  })
  .post('/pair/skip', sValidator('json', skipRequest, rejectInvalid), async (c) => {
    const now = Date.now();
    const actor = await requireActor(c, now);
    return c.json(await skip(c.env.DB, c.env.ACTOR_KEY, actor, c.req.valid('json').pair, now));
  })
  .post('/votes', sValidator('json', voteRequest, rejectInvalid), async (c) => {
    const now = Date.now();
    const actor = await requireActor(c, now);
    await record(c.env.DB, actor, c.get('tag'), c.req.valid('json'), now);
    return c.body(null, 204);
  })
  .post('/votes/undo', sValidator('json', undoRequest, rejectInvalid), async (c) => {
    const now = Date.now();
    const actor = await requireActor(c, now);
    await undo(c.env.DB, actor, c.req.valid('json').family, now);
    return c.body(null, 204);
  })
  .get('/freshness', sValidator('query', freshnessQuery, rejectInvalid), async (c) => {
    const status = await cachedFreshness(c.req.url, () =>
      freshness(c.env.DB, c.env.STORE, c.req.valid('query')),
    );
    return c.json({ status });
  })
  .notFound((c) => c.json({ error: 'not_found' } satisfies ErrorView, 404))
  .onError((error, c) => {
    if (error instanceof HTTPException) {
      error = new ApiError(400, 'invalid_request');
    }
    if (error instanceof ApiError) {
      const body: ErrorView =
        error.code === 'admission_required'
          ? { error: error.code, sitekey: c.env.TURNSTILE_SITEKEY }
          : { error: error.code };
      return c.json(body, error.status);
    }
    // With invocation logs off, this record is all Workers Logs keep: never an address or cookie.
    console.error({
      route: `${c.req.method} ${routePath(c)}`,
      message: error.message,
      stack: error.stack,
    });
    return c.json({ error: 'unavailable' } satisfies ErrorView, 500);
  });

export default {
  fetch: app.fetch,
  scheduled: (controller, env, ctx) => {
    ctx.waitUntil(retain(env.DB, controller.scheduledTime));
  },
} satisfies ExportedHandler<Bindings>;
