import type { D1Migration } from 'cloudflare:test';

import type * as worker from '../index';

interface SchemaColumn {
  affinity: 'INTEGER' | 'TEXT';
  nullable: boolean;
  generated: boolean;
}

declare global {
  namespace Cloudflare {
    /** The bindings the tests read, with what `vitest.config.ts` adds for them. */
    interface Env {
      DB: D1Database;
      IP_TAG_KEY: string;
      TEST_MIGRATIONS: D1Migration[];
      TEST_PUBLICATION: string;
      TEST_SCHEMA: Record<string, Record<string, SchemaColumn>>;
      TURNSTILE_HOSTNAME: string;
    }

    /** The environment-only `main`, which `wrangler types` leaves out. */
    interface GlobalProps {
      mainModule: typeof worker;
    }
  }
}
