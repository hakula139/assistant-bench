import { createRemoteJWKSet, jwtVerify } from 'jose';
import * as z from 'zod/mini';

const owners = z.array(z.email()).check(z.length(2));
const keySets = new Map<string, ReturnType<typeof createRemoteJWKSet>>();

/** Verifies the private application's Access identity before any route or asset is served. */
export async function verifyOwner(request: Request, env: Cloudflare.PrivateEnv): Promise<boolean> {
  const token = request.headers.get('Cf-Access-Jwt-Assertion');
  if (token === null) {
    return false;
  }
  try {
    const allowed = owners.parse(JSON.parse(env.PRIVATE_OWNER_EMAILS));
    let keys = keySets.get(env.ACCESS_ISSUER);
    if (keys === undefined) {
      keys = createRemoteJWKSet(new URL('/cdn-cgi/access/certs', env.ACCESS_ISSUER));
      keySets.set(env.ACCESS_ISSUER, keys);
    }
    const { payload } = await jwtVerify(token, keys, {
      issuer: env.ACCESS_ISSUER,
      audience: env.ACCESS_AUDIENCE,
      algorithms: ['RS256'],
      requiredClaims: ['exp', 'sub', 'email'],
    });
    return (
      typeof payload.sub === 'string' &&
      payload.sub !== '' &&
      typeof payload.email === 'string' &&
      allowed.includes(payload.email)
    );
  } catch {
    return false;
  }
}
