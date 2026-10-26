import { Console } from 'node:console';
import { createInterface } from 'node:readline';
import { getPlatformProxy } from 'wrangler';

globalThis.console = new Console({ stdout: process.stderr, stderr: process.stderr });

const platform = await getPlatformProxy({
  configPath: process.argv[2],
  envFiles: ['/dev/null'],
  persist: false,
});

try {
  const input = createInterface({ input: process.stdin });
  const management = platform.env.MANAGEMENT;

  for await (const line of input) {
    let id = null;
    let envelope;

    try {
      const request = JSON.parse(line);
      id = request.id;

      if (!Number.isSafeInteger(id) || id < 0) {
        throw new Error('Invalid request identity');
      }

      if (request.operation !== 'command') {
        throw new Error('Unknown operation');
      }

      const response = await management.command(JSON.stringify(request.request));
      envelope = JSON.parse(response);
    } catch (error) {
      envelope = {
        error: {
          reason: 'unavailable',
          detail: error instanceof Error ? error.message : String(error),
        },
      };
    }

    process.stdout.write(JSON.stringify({ id, ...envelope }) + '\n');
  }
} finally {
  await platform.dispose();
}
