import astro from 'eslint-plugin-astro';
import reactHooks from 'eslint-plugin-react-hooks';
import { defineConfig } from 'eslint/config';
import tseslint from 'typescript-eslint';

const sharedRules = {
  'curly': ['error', 'all'],
  'eqeqeq': ['error', 'always'],
  'no-throw-literal': 'error',
  'prefer-const': 'error',
};

export default defineConfig([
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
    ignores: ['.astro/', '.wrangler/', 'dist/', 'node_modules/'],
  },
]);
