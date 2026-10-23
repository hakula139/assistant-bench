import type { APIRoute } from 'astro';

import { getFamilies, redirectLines } from '@/lib/reports';

export const prerender = true;

/** The `_redirects` file that Cloudflare Workers static assets reads at the site root. */
export const GET: APIRoute = async () =>
  new Response(redirectLines(await getFamilies()), {
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
  });
