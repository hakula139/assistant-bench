// `astro check` reads `.astro` modules itself, while plain TypeScript, which ESLint's typed rules
// run on, sees only this shape.
declare module '*.astro' {
  import type { AstroComponentFactory } from 'astro/runtime/server/index.js';

  const component: AstroComponentFactory;
  export default component;
}
