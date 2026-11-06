import react from '@astrojs/react';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig, envField } from 'astro/config';

import voting from './src/integrations/voting';

const pages = process.env.CI_PAGES_URL;

export default defineConfig({
  site: pages ?? 'https://bench.hakula.xyz',
  base: pages ? new URL(pages).pathname : '/',
  env: {
    schema: {
      CI_PAGES_URL: envField.string({ context: 'client', access: 'public', optional: true }),
      CI_PROJECT_URL: envField.string({ context: 'client', access: 'public', optional: true }),
    },
  },
  security: {
    csp: {
      styleDirective: { resources: ["'self'", "'unsafe-inline'"] },
    },
  },
  integrations: [react(), ...(pages ? [] : [voting()])],
  vite: {
    plugins: [tailwindcss()],
  },
});
