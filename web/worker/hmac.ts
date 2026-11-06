const encoder = new TextEncoder();
const imported = new Map<string, Promise<CryptoKey>>();

function importedKey(key: string): Promise<CryptoKey> {
  let cryptoKey = imported.get(key);
  if (cryptoKey === undefined) {
    cryptoKey = crypto.subtle.importKey(
      'raw',
      encoder.encode(key),
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['sign'],
    );
    imported.set(key, cryptoKey);
  }
  return cryptoKey;
}

/** HMAC-SHA-256 under `key`, which each isolate imports once. */
export function signer(key: string): (message: string) => Promise<Uint8Array> {
  const cryptoKey = importedKey(key);
  return async (message) =>
    new Uint8Array(await crypto.subtle.sign('HMAC', await cryptoKey, encoder.encode(message)));
}

export function hex(bytes: Uint8Array): string {
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}
