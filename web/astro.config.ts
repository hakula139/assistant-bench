import react from '@astrojs/react';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'astro/config';

import voting from './src/integrations/voting';

const pages = process.env.CI_PAGES_URL;

export default defineConfig({
  site: pages ?? 'https://bench.hakula.xyz',
  base: pages ? new URL(pages).pathname : '/',
  security: {
    csp: {
      styleDirective: { resources: ["'self'", "'unsafe-inline'"] },
    },
  },
  integrations: [react(), voting()],
  vite: {
    plugins: [tailwindcss()],
  },
});
