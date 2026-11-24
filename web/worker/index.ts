import { sValidator } from '@hono/standard-validator';
import { Hono, type Context } from 'hono';
import { bodyLimit } from 'hono/body-limit';
import { HTTPException } from 'hono/http-exception';
import { routePath } from 'hono/route';

import {
  admissionRequest,
  familyQuery,
  pairBatchRequest,
  previewRequest,
  skipRequest,
  UNDO_HEADER,
  UNDO_NONE,
  undoRequest,
  voteBatchRequest,
  voteRequest,
  type ErrorView,
} from '@/lib/arena';

import { verifyOwner } from './access';
import {
  admit,
  admitOwner,
  claimedActor,
  ipTag,
  issueCookie,
  knownActor,
  requireActor,
  verifyChallenge,
} from './actors';
import { ApiError, type Api, type Bindings } from './api';
import { all, queries } from './db';
import { BODY_LIMIT_BYTES } from './limits';
import { pairBatch, pairState, skip, type PairSnapshot } from './pairs';
import { preview, PREVIEW_HEADERS, PREVIEW_UNAVAILABLE } from './previews';
import { retain } from './retention';
import { confirm, record, recordBatch, undo } from './votes';

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

function isPreview(c: Context<Api>): boolean {
  return c.req.path.startsWith('/api/previews/');
}

function applyPreviewHeaders(c: Pick<Context<Api>, 'header'>): void {
  for (const [name, value] of Object.entries(PREVIEW_HEADERS)) {
    c.header(name, value);
  }
}

function pairResponse(c: Context<Api>, { state, undoPair }: PairSnapshot) {
  c.header(UNDO_HEADER, undoPair ?? UNDO_NONE);
  return c.json(state);
}

function respondError(error: Error, c: Context<Api>) {
  if (error instanceof HTTPException) {
    error = new ApiError(400, 'invalid_request');
  }
  if (error instanceof ApiError) {
    if (isPreview(c)) {
      return c.text(PREVIEW_UNAVAILABLE, error.status);
    }

    const body: ErrorView =
      error.code === 'admission_required' && c.env.STORE !== 'private'
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
  return isPreview(c)
    ? c.text(PREVIEW_UNAVAILABLE, 500)
    : c.json({ error: 'unavailable' } satisfies ErrorView, 500);
}

const api = new Hono<Api>()
  .basePath('/api')
  .use('/previews/*', async (c, next) => {
    await next();
    applyPreviewHeaders(c);
  })
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
    if (c.env.STORE === 'private' && c.get('claimed') !== c.env.PRIVATE_ACTOR_ID) {
      throw new ApiError(401, 'reload_required');
    }

    const { success } = await c.env.RATE_LIMIT.limit({ key: c.get('claimed') ?? c.get('tag') });
    if (!success) {
      throw new ApiError(429, 'rate_limited');
    }
    await next();
  })
  .get('/families', async (c) => {
    const families = await all(
      c.env.DB,
      queries
        .selectFrom('active')
        .innerJoin('publications', 'publications.id', 'active.publication_id')
        .select(['active.family', 'publications.display_name', 'publications.aspect'])
        .orderBy('active.family'),
    );
    return c.json({ families });
  })
  .post('/actors', sValidator('json', admissionRequest, rejectInvalid), async (c) => {
    const now = Date.now();
    if (c.env.STORE === 'private') {
      await requireActor(c, now);
      return c.body(null, 204);
    }

    if (!(await knownActor(c.env.DB, c.get('claimed'), now))) {
      await verifyChallenge(c.env, c.req.valid('json').token, address(c.req.raw));
      await issueCookie(c, await admit(c.env.DB, c.get('tag'), now));
    }
    return c.body(null, 204);
  })
  .get('/pair', sValidator('query', familyQuery, rejectInvalid), async (c) => {
    const now = Date.now();
    const actor = await requireActor(c, now);
    return pairResponse(
      c,
      await pairState(c.env.DB, c.env.ACTOR_KEY, actor, c.req.valid('query').family, now),
    );
  })
  .post('/pair/batch', sValidator('json', pairBatchRequest, rejectInvalid), async (c) => {
    const now = Date.now();
    const actor = await requireActor(c, now);
    const { family, count } = c.req.valid('json');
    const pairs = await pairBatch(c.env.DB, c.env.ACTOR_KEY, actor, family, count, now);
    return c.json({ pairs });
  })
  .post('/pair/skip', sValidator('json', skipRequest, rejectInvalid), async (c) => {
    const now = Date.now();
    const actor = await requireActor(c, now);
    return pairResponse(
      c,
      await skip(c.env.DB, c.env.ACTOR_KEY, actor, c.req.valid('json').pair, now),
    );
  })
  .post('/votes', sValidator('json', voteRequest, rejectInvalid), async (c) => {
    const now = Date.now();
    const actor = await requireActor(c, now);
    await record(c.env.DB, actor, c.get('tag'), c.req.valid('json'), now);
    return c.body(null, 204);
  })
  .post('/votes/batch', sValidator('json', voteBatchRequest, rejectInvalid), async (c) => {
    const now = Date.now();
    const actor = await requireActor(c, now);
    const { family, votes } = c.req.valid('json');
    return c.json({ votes: await recordBatch(c.env.DB, actor, c.get('tag'), votes, now, family) });
  })
  .post('/votes/confirm', sValidator('json', voteRequest, rejectInvalid), async (c) => {
    const actor = await requireActor(c, Date.now());
    await confirm(c.env.DB, actor, c.req.valid('json'));
    return c.body(null, 204);
  })
  .post('/votes/undo', sValidator('json', undoRequest, rejectInvalid), async (c) => {
    const now = Date.now();
    const actor = await requireActor(c, now);
    await undo(c.env.DB, actor, c.req.valid('json'), now);
    return c.body(null, 204);
  })
  .get('/previews/:pair/:side', sValidator('param', previewRequest, rejectInvalid), async (c) => {
    const actor = await requireActor(c, Date.now());
    const { pair, side } = c.req.valid('param');
    return c.html(await preview(c.env.DB, actor, pair, side));
  });

export const app = new Hono<Api>()
  .use(async (c, next) => {
    if (c.env.STORE === 'private') {
      if (
        new URL(c.req.url).origin !== c.env.APP_ORIGIN ||
        !(await verifyOwner(c.req.raw, c.env))
      ) {
        c.res = isPreview(c)
          ? c.text(PREVIEW_UNAVAILABLE, 403)
          : c.json({ error: 'forbidden' } satisfies ErrorView, 403);
      } else {
        await next();
      }

      if (isPreview(c)) {
        applyPreviewHeaders(c);
      } else {
        c.header('Cache-Control', 'no-store');
      }

      const policy = (c.res.headers.get('Content-Security-Policy') ?? '')
        .split(';')
        .map((directive) => directive.trim())
        .filter((directive) => directive !== '' && !directive.startsWith('frame-ancestors '));
      policy.push("frame-ancestors 'self'");
      c.header('Content-Security-Policy', policy.join('; '));
      c.header('X-Frame-Options', 'SAMEORIGIN');
      return;
    }

    await next();
  })
  .route('/', api)
  .all('*', async (c) => {
    if (c.env.STORE !== 'private' || c.req.path.startsWith('/api/')) {
      return c.notFound();
    }

    c.res = await c.env.ASSETS.fetch(c.req.raw);
    if (
      c.req.method === 'GET' &&
      c.req.path === '/vote/' &&
      c.res.ok &&
      c.res.headers.get('Content-Type')?.startsWith('text/html')
    ) {
      await admitOwner(c.env.DB, c.env.PRIVATE_ACTOR_ID, Date.now());
      await issueCookie(c, c.env.PRIVATE_ACTOR_ID);
    }
    return c.res;
  })
  .notFound((c) =>
    isPreview(c)
      ? c.text(PREVIEW_UNAVAILABLE, 404)
      : c.json({ error: 'not_found' } satisfies ErrorView, 404),
  )
  .onError(respondError);

export default {
  fetch: app.fetch,
  scheduled: (controller, env, ctx) => {
    ctx.waitUntil(retain(env.DB, controller.scheduledTime));
  },
} satisfies ExportedHandler<Bindings>;
