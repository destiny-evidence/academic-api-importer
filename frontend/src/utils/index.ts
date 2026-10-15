const EMPTY_PLACEHOLDER = '—';

/** `2026-10-01 20:13:45`, or the original text when it cannot be parsed. */
export function formatTimeStamp(createdAt?: string | number | null | undefined): string {
  if (!createdAt) return EMPTY_PLACEHOLDER;
  const date = new Date(createdAt);
  if (Number.isNaN(date.getTime())) return `${createdAt}`;
  const pad = (value: number) => String(value).padStart(2, '0');
  const day = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  const time = `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  return `${day} ${time}`;
}

/** Escapes a plain-text message for the `v-html` binding in `ToastsViewer`. */
export function escapeHtml(text: string): string {
  const entities: Record<string, string> = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  };
  return text.replace(/[&<>"']/g, (char) => entities[char]!);
}

export function createRandomString(length: number): string {
  const chars =
    'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!?§$%&/()"\';:_-.,#+~*^°üäöß\\';
  let result = '';
  for (let i = 0; i < length; i++) {
    result += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return result;
}

export function formatNumber(
  value: string | number | null | undefined,
  decimals: number = 2,
): string {
  // 1. Return "—" for null or undefined
  if (value === null || value === undefined) {
    return EMPTY_PLACEHOLDER;
  }

  // 2. Parse string inputs to numbers
  let numericValue: number;

  if (typeof value === 'string') {
    const trimmed = value.trim();
    // Return "—" for empty strings or invalid numbers (NaN)
    if (trimmed === '') return EMPTY_PLACEHOLDER;
    numericValue = Number(trimmed);
  } else {
    numericValue = value;
  }

  // Handle NaN (e.g., formatNumber("invalid") or formatNumber(NaN))
  if (Number.isNaN(numericValue)) {
    return EMPTY_PLACEHOLDER;
  }
  const locale = typeof window !== 'undefined' ? navigator.language : 'en-US';
  return new Intl.NumberFormat(locale, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(numericValue);
}

/** Reads the first value of a query parameter, which may repeat as a list. */
export function queryParam(
  value: string | null | (string | null)[] | undefined,
): string | undefined {
  return (Array.isArray(value) ? value[0] : value) ?? undefined;
}
