import type { AstroIntegration } from 'astro';

export default function voting(): AstroIntegration {
  return {
    name: 'voting',
    hooks: {
      'astro:config:setup': ({ injectRoute }) => {
        injectRoute({ pattern: '/vote', entrypoint: './src/vote/vote.astro' });
      },
    },
  };
}
