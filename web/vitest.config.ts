/// <reference types="vitest/config" />
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

import { cloudflareTest, readD1Migrations } from '@cloudflare/vitest-plugin';
import { getViteConfig } from 'astro/config';
import ts from 'typescript';

interface SchemaColumn {
  [key: string]: string | boolean;
  affinity: 'INTEGER' | 'TEXT';
  nullable: boolean;
  generated: boolean;
}

function classifyColumn(checker: ts.TypeChecker, type: ts.Type): SchemaColumn {
  const selectProp = type.getProperty('__select__');
  const insertProp = type.getProperty('__insert__');
  let generated = false;
  let valueType = type;

  if (selectProp !== undefined && insertProp !== undefined) {
    const insertType = checker.getTypeOfSymbol(insertProp);
    const insertMembers = insertType.isUnion() ? insertType.types : [insertType];
    generated = insertMembers.some((member) => (member.flags & ts.TypeFlags.Undefined) !== 0);
    valueType = checker.getTypeOfSymbol(selectProp);
  }

  const members = valueType.isUnion() ? valueType.types : [valueType];
  const nullable = members.some((member) => (member.flags & ts.TypeFlags.Null) !== 0);
  const nonNull = members.filter((member) => (member.flags & ts.TypeFlags.Null) === 0);
  const isInteger =
    nonNull.length > 0 &&
    nonNull.every(
      (member) => (member.flags & (ts.TypeFlags.Number | ts.TypeFlags.NumberLiteral)) !== 0,
    );
  const isString =
    nonNull.length > 0 &&
    nonNull.every(
      (member) => (member.flags & (ts.TypeFlags.String | ts.TypeFlags.StringLiteral)) !== 0,
    );

  if (!isInteger && !isString) {
    throw new Error(`Unsupported column type: ${checker.typeToString(type)}`);
  }

  return { affinity: isInteger ? 'INTEGER' : 'TEXT', nullable, generated };
}

function readDatabaseSchema(): Record<string, Record<string, SchemaColumn>> {
  const configPath = fileURLToPath(new URL('worker/tsconfig.json', import.meta.url));
  const configFile = ts.readConfigFile(configPath, (path) => ts.sys.readFile(path));
  if (configFile.error !== undefined) {
    throw new Error(ts.flattenDiagnosticMessageText(configFile.error.messageText, '\n'));
  }

  const parsed = ts.parseJsonConfigFileContent(
    configFile.config,
    ts.sys,
    fileURLToPath(new URL('worker', import.meta.url)),
  );
  const schemaPath = fileURLToPath(new URL('worker/schema.ts', import.meta.url));
  const program = ts.createProgram([schemaPath], { ...parsed.options, noEmit: true });
  const sourceFile = program.getSourceFile(schemaPath);
  if (sourceFile === undefined) {
    throw new Error(`Could not load ${schemaPath}.`);
  }

  const checker = program.getTypeChecker();
  const moduleSymbol = checker.getSymbolAtLocation(sourceFile);
  const databaseSymbol = moduleSymbol?.exports?.get(ts.escapeLeadingUnderscores('Database'));
  if (databaseSymbol === undefined) {
    throw new Error('Could not locate exported Database interface in worker/schema.ts.');
  }

  const databaseType = checker.getDeclaredTypeOfSymbol(databaseSymbol);
  const tables: Record<string, Record<string, SchemaColumn>> = {};

  for (const tableSymbol of checker.getPropertiesOfType(databaseType)) {
    const tableType = checker.getTypeOfSymbol(tableSymbol);
    const columns: Record<string, SchemaColumn> = {};
    for (const columnSymbol of checker.getPropertiesOfType(tableType)) {
      columns[columnSymbol.name] = classifyColumn(checker, checker.getTypeOfSymbol(columnSymbol));
    }
    tables[tableSymbol.name] = columns;
  }

  return tables;
}

export default getViteConfig({
  test: {
    unstubGlobals: true,
    restoreMocks: true,
    coverage: {
      enabled: true,
      // workerd exposes no V8 coverage, so both projects instrument their sources.
      provider: 'istanbul',
      reportsDirectory: '../coverage',
      include: ['src/**/*.{astro,ts,tsx}', 'worker/**/*.ts'],
      exclude: ['src/components/ui/**', 'src/test/**', 'worker/test/**'],
      // Codecov matches lcov paths against the repository, so they start at its root.
      reporter: ['text-summary', ['lcovonly', { projectRoot: '..' }]],
    },
    projects: [
      {
        extends: true,
        test: { name: 'site', include: ['src/**/*.test.{ts,tsx}'] },
      },
      {
        plugins: [
          cloudflareTest(async () => ({
            wrangler: { configPath: './wrangler.toml', environment: 'local' },
            miniflare: {
              bindings: {
                ACTOR_KEY: 'test-actor-key',
                IP_TAG_KEY: 'test-ip-tag-key',
                STORE: 'production',
                TEST_MIGRATIONS: await readD1Migrations('migrations'),
                TEST_PUBLICATION: await readFile(
                  new URL('../tests/fixtures/arena-publication.sql', import.meta.url),
                  'utf8',
                ),
                TEST_SCHEMA: readDatabaseSchema(),
              },
            },
          })),
        ],
        resolve: { alias: { '@': fileURLToPath(new URL('src', import.meta.url)) } },
        test: {
          name: 'worker',
          include: ['worker/**/*.test.ts'],
          setupFiles: ['worker/test/setup.ts'],
          restoreMocks: true,
        },
      },
    ],
  },
});
