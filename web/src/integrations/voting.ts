import { fileURLToPath } from 'node:url';

import type { AstroIntegration } from 'astro';

export default function voting(): AstroIntegration {
  return {
    name: 'voting',
    hooks: {
      'astro:config:setup': ({ injectRoute, updateConfig }) => {
        injectRoute({ pattern: '/vote', entrypoint: './src/vote/vote.astro' });
        injectRoute({ pattern: '/admission-shell', entrypoint: './src/admission/document.astro' });
        updateConfig({
          vite: {
            plugins: [
              {
                name: 'admission-client',
                apply: 'build',
                buildStart() {
                  this.emitFile({
                    type: 'chunk',
                    id: fileURLToPath(new URL('../admission/client.ts', import.meta.url)),
                    fileName: 'admission.js',
                  });
                },
              },
              {
                name: 'admission-client-dev',
                apply: 'serve',
                configureServer(server) {
                  server.middlewares.use('/admission.js', (_request, response, next) => {
                    void server.transformRequest('/src/admission/client.ts').then((result) => {
                      if (result === null) {
                        next();
                        return;
                      }
                      response.setHeader('Content-Type', 'application/javascript');
                      response.end(result.code);
                    }, next);
                  });
                },
              },
            ],
          },
        });
      },
    },
  };
}
