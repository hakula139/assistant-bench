import react from '@astrojs/react';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'astro/config';

import localVote from './src/integrations/local-vote';
import redirects from './src/integrations/redirects';

export default defineConfig({
  site: 'https://bench.hakula.xyz',
  integrations: [react(), localVote(), redirects()],
  vite: {
    plugins: [tailwindcss()],
  },
});
