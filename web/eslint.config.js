import astro from 'eslint-plugin-astro';
import perfectionist from 'eslint-plugin-perfectionist';
import reactHooks from 'eslint-plugin-react-hooks';
import { defineConfig } from 'eslint/config';
import tseslint from 'typescript-eslint';

const sharedRules = {
  'curly': ['error', 'all'],
  'eqeqeq': ['error', 'always'],
  'no-throw-literal': 'error',
  'prefer-const': 'error',
  'perfectionist/sort-imports': [
    'error',
    {
      groups: [
        ['type-builtin', 'value-builtin'],
        ['type-external', 'value-external'],
        ['type-internal', 'value-internal'],
        'unknown',
      ],
      internalPattern: ['^@/'],
    },
  ],
  'perfectionist/sort-named-imports': ['error', { groups: ['value-import', 'type-import'] }],
};

const classicZod =
  "Build classic Zod schemas only at build time. Constructing one runs Zod's eval probe, which the pages' script policy reports as a violation.";

export default defineConfig([
  {
    plugins: { perfectionist },
  },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [tseslint.configs.strictTypeChecked, reactHooks.configs.flat.recommended],
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      ...sharedRules,
      '@typescript-eslint/consistent-type-imports': [
        'error',
        { prefer: 'type-imports', fixStyle: 'separate-type-imports' },
      ],
      '@typescript-eslint/restrict-template-expressions': ['error', { allowNumber: true }],
      '@typescript-eslint/switch-exhaustiveness-check': 'error',
    },
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
    files: ['**/*.astro'],
    extends: [astro.configs.recommended],
    rules: sharedRules,
  },
  {
    files: ['**/*.js'],
    extends: [tseslint.configs.recommended],
    rules: sharedRules,
  },
  {
    ignores: ['.astro/', '.wrangler/', 'dist/', 'node_modules/', 'worker-configuration.d.ts'],
  },
]);
