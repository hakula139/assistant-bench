import type { AstroIntegration } from 'astro';

/** Writes the site's redirect rules beside the pages, since no page route may start with `_`. */
export default function redirects(): AstroIntegration {
  return {
    name: 'redirects',
    hooks: {
      'astro:config:setup': ({ injectRoute }) => {
        injectRoute({
          pattern: '/_redirects',
          entrypoint: './src/redirects.ts',
          prerender: true,
        });
      },
    },
  };
}
