import base from '@hakula139/eslint-config';
import typescript from '@hakula139/eslint-config/typescript';
import astro from 'eslint-plugin-astro';
import reactHooks from 'eslint-plugin-react-hooks';
import { defineConfig } from 'eslint/config';
import globals from 'globals';
import tseslint from 'typescript-eslint';

const classicZod =
  "Build classic Zod schemas only at build time. Constructing one runs Zod's eval probe, which the pages' script policy reports as a violation.";

const namedZod =
  "Import zod/mini as a namespace, since the named `z` export keeps all of Zod in the Worker's bundle.";

export default defineConfig([
  base,
  typescript,
  {
    files: ['**/*.{ts,tsx}'],
    extends: [reactHooks.configs.flat.recommended],
    languageOptions: {
      parserOptions: {
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },
  {
    files: ['**/*.astro'],
    extends: [tseslint.configs.recommended],
  },
  astro.configs.recommended,
  {
    files: ['**/*.astro/*.ts'],
    extends: [tseslint.configs.disableTypeChecked],
  },
  {
    files: ['src/**/*.{ts,tsx}'],
    ignores: [
      'src/content.config.ts',
      'src/lib/family.ts',
      'src/lib/report.ts',
      'src/**/*.test.{ts,tsx}',
      'src/test/**',
    ],
    rules: {
      '@typescript-eslint/no-restricted-imports': [
        'error',
        {
          paths: [
            ...['astro/zod', 'zod'].map((name) => ({ name, message: classicZod })),
            ...['@/lib/family', '@/lib/report'].map((name) => ({
              name,
              allowTypeImports: true,
              message: classicZod,
            })),
          ],
          patterns: [{ group: ['zod/*', '!zod/mini'], message: classicZod }],
        },
      ],
    },
  },
  {
    files: ['worker/**/*.ts'],
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          selector:
            "ImportDeclaration[source.value='zod/mini'] > ImportSpecifier[imported.name='z']",
          message: namedZod,
        },
      ],
    },
  },
  {
    files: ['eslint.config.js'],
    languageOptions: { globals: globals.node },
  },
  {
    ignores: ['.astro/', '.wrangler/', 'dist/', 'worker-configuration.d.ts'],
  },
]);
