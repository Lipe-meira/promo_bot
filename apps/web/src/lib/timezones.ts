const FALLBACK = [
  "America/Sao_Paulo",
  "America/Manaus",
  "America/Fortaleza",
  "America/Bahia",
  "America/Rio_Branco",
  "America/Noronha",
  "Europe/Lisbon",
  "UTC",
];

/** IANA zones for the picker; the API remains the judge of validity. */
export function timezoneOptions(): string[] {
  try {
    const zones = Intl.supportedValuesOf("timeZone");
    if (zones.length > 0) return zones.includes("UTC") ? zones : [...zones, "UTC"];
  } catch {
    // Older engines: fall back to a short list.
  }
  return FALLBACK;
}
