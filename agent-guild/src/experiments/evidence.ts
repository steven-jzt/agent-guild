export function redact(value: unknown): unknown {
  if (typeof value === 'string') return value
    .replace(/Bearer\s+[^\s"'\\]+/gi, 'Bearer [REDACTED]')
    .replace(/\b(?:sk|dsk|ds)-[a-zA-Z0-9_-]{12,}/g, '[REDACTED]')
    .replace(/\beyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b/g, '[REDACTED]');
  if (Array.isArray(value)) return value.map(redact);
  if (value !== null && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, entry]) => [key,
    /^(?:api[_-]?key|(?:access|refresh)[_-]?token|authorization|password|secret|cookie)$/i.test(key) ? '[REDACTED]' : redact(entry),
  ]));
  return value;
}
