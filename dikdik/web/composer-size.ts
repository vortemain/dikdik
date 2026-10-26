/** Leave room for navigation, controls and a readable piece of the conversation. */
export function composerMaximum(viewport: number, controls: number): number {
  return Math.max(52, Math.min(520, viewport - controls - 180));
}

export class ComposerSize {
  private manual: number | null = null;
  private previous = '';
  reset(): void { this.manual = null; }
  resize(height: number, maximum: number): void { this.manual = Math.max(52, Math.min(maximum, height)); }
  fit(content: string, natural: number, maximum: number): number {
    if (!content && this.previous) this.reset();
    this.previous = content;
    return Math.max(52, Math.min(maximum, this.manual ?? natural));
  }
}
