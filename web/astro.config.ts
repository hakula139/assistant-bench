import react from '@astrojs/react';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'astro/config';

import voting from './src/integrations/voting';

export default defineConfig({
  site: 'https://bench.hakula.xyz',
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
