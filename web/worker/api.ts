import type { ContentfulStatusCode } from 'hono/utils/http-status';

import type { ErrorCode } from '@/lib/arena';

/** The bindings of every environment that runs the Worker, which the preview Worker lacks. */
export type Bindings = Cloudflare.ProductionEnv | Cloudflare.StagingEnv | Cloudflare.LocalEnv;

/** The Hono environment of every route. */
export interface Api {
  Bindings: Bindings;
  Variables: {
    /** The keyed tag of the request's network. */
    tag: string;
    /** The actor the request's cookie names under a valid signature, not yet looked up. */
    claimed: string | null;
  };
}

/** A failure the browser sees as its fixed code, with no other detail. */
export class ApiError extends Error {
  constructor(
    readonly status: ContentfulStatusCode,
    readonly code: ErrorCode,
  ) {
    super(code);
  }
}
