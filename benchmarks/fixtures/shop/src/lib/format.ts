/** Prices are whole Kenyan shillings with thousands separators, e.g. "KSh 1,235". */
export function formatPrice(amount: number): string {
  return "KSh " + Math.round(amount).toLocaleString("en-KE");
}
