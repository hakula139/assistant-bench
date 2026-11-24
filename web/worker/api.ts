import type { ContentfulStatusCode } from 'hono/utils/http-status';

import type { ErrorCode } from '@/lib/arena';

export type PublicBindings = Cloudflare.ProductionEnv | Cloudflare.StagingEnv | Cloudflare.LocalEnv;

/** The bindings of every environment that runs the Worker, which the preview Worker lacks. */
export type Bindings = PublicBindings | Cloudflare.PrivateEnv;

export interface Api {
  Bindings: Bindings;
  Variables: {
    tag: string;
    claimed: string | null;
  };
}

export class ApiError extends Error {
  constructor(
    readonly status: ContentfulStatusCode,
    readonly code: ErrorCode,
  ) {
    super(code);
  }
}
