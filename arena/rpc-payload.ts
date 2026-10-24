export function payload(value: unknown, maximum: number): string {
  if (
    typeof value !== 'string' ||
    value.length > maximum ||
    new TextEncoder().encode(value).byteLength > maximum
  ) {
    throw new TypeError('Invalid service request.');
  }

  return value;
}
