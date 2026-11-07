import { fileURLToPath } from 'node:url';

import { expect, test } from 'vitest';
import { unstable_readConfig } from 'wrangler';

const CONFIG = fileURLToPath(new URL('../../wrangler.toml', import.meta.url));

interface Deployment {
  assets?: { run_worker_first?: boolean | string[] };
  d1_databases: { binding: string; database_id?: string }[];
  secrets?: { required: string[] };
}

function configuration(environment: string): Deployment {
  const config: unknown = unstable_readConfig({ config: CONFIG, env: environment });
  return config as Deployment;
}

test.each(['production', 'staging'])(
  'keeps request headers, and with them addresses, out of Workers Logs in %s',
  (environment) => {
    const config: unknown = unstable_readConfig({ config: CONFIG, env: environment });

    expect(config).toMatchObject({
      observability: { logs: { enabled: true, invocation_logs: false } },
    });
  },
);

test('protects every private asset route and binds a database distinct from both public stores', () => {
  const privateConfig = configuration('private');
  const publicConfigs = ['production', 'staging'].map(configuration);

  expect(privateConfig).toMatchObject({
    workers_dev: false,
    preview_urls: false,
    assets: { binding: 'ASSETS', run_worker_first: true },
    vars: { STORE: 'private' },
  });
  const database = privateConfig.d1_databases.find(({ binding }) => binding === 'DB');
  expect(database?.database_id).toBeTruthy();
  for (const config of publicConfigs) {
    expect(config.assets?.run_worker_first).toEqual(['/api/*']);
    expect(config.d1_databases.find(({ binding }) => binding === 'DB')?.database_id).not.toBe(
      database?.database_id,
    );
  }
  expect(privateConfig.secrets?.required).toEqual(
    expect.arrayContaining([
      'ACTOR_KEY',
      'IP_TAG_KEY',
      'ACCESS_ISSUER',
      'ACCESS_AUDIENCE',
      'PRIVATE_OWNER_EMAILS',
      'PRIVATE_ACTOR_ID',
    ]),
  );
});
