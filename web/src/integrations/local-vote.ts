import type { AstroIntegration } from 'astro';

export const localBuild = process.env.BENCH_LOCAL === '1';

export default function localVote(): AstroIntegration {
  return {
    name: 'local-vote',
    hooks: {
      'astro:config:setup': ({ injectRoute }) => {
        if (localBuild) {
          injectRoute({ pattern: '/vote', entrypoint: './src/vote/vote.astro' });
        }
      },
    },
  };
}
