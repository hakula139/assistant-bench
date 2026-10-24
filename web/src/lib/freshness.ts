import { z } from 'astro/zod';

const freshness = z.object({ status: z.enum(['current', 'stale', 'unknown']) }).strict();
export type Freshness = z.infer<typeof freshness>['status'];

export async function queryFreshness(
  exportId: string,
  liveOrigin: string | undefined,
  signal: AbortSignal,
): Promise<Freshness> {
  if (liveOrigin === undefined || window.location.origin !== liveOrigin) {
    return 'unknown';
  }

  try {
    const response = await fetch('/api/freshness', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ export_id: exportId }),
      signal,
    });
    if (!response.ok) {
      return 'unknown';
    }

    const parsed = freshness.safeParse(await response.json());
    return parsed.success ? parsed.data.status : 'unknown';
  } catch {
    return 'unknown';
  }
}
