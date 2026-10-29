import { fileURLToPath } from 'node:url';

import { expect, test } from 'vitest';
import { unstable_readConfig } from 'wrangler';

const CONFIG = fileURLToPath(new URL('../../wrangler.toml', import.meta.url));

test.each(['production', 'staging'])(
  'keeps request headers, and with them addresses, out of Workers Logs in %s',
  (environment) => {
    const config: unknown = unstable_readConfig({ config: CONFIG, env: environment });

    expect(config).toMatchObject({
      observability: { logs: { enabled: true, invocation_logs: false } },
    });
  },
);
