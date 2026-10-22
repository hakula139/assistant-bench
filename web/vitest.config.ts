/// <reference types="vitest/config" />
import { getViteConfig } from 'astro/config';

export default getViteConfig({
  test: {
    include: ['src/**/*.test.{ts,tsx}'],
    unstubGlobals: true,
    restoreMocks: true,
    coverage: {
      enabled: true,
      provider: 'v8',
      include: ['src/**/*.{astro,ts,tsx}'],
      exclude: ['src/components/ui/**', 'src/test/**'],
      // Codecov matches lcov paths against the repository, so they start at its root.
      reporter: ['text-summary', ['lcovonly', { projectRoot: '..' }]],
    },
  },
});
