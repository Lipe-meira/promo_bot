/** Base URL of the API. Public by nature (it is called from the browser); it carries no secret. */
export function apiBaseUrl(): string {
  const value = process.env.NEXT_PUBLIC_API_URL;
  if (!value) {
    throw new Error("NEXT_PUBLIC_API_URL is not set (see apps/web/.env.example)");
  }
  return value;
}
