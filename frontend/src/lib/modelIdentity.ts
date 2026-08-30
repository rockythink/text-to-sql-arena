export function displayModelName(name: string): string {
  return name.replace(/\s*本机实测\s*/g, " ").replace(/\s{2,}/g, " ").trim();
}
