export class JsonLines {
  private buffer = '';
  private readonly limit: number;
  constructor(limit = 8 * 1024 * 1024) { this.limit = limit; }

  push(chunk: string): unknown[] {
    this.buffer += chunk;
    const frames: unknown[] = [];
    let end: number;
    while ((end = this.buffer.indexOf('\n')) >= 0) {
      if (end > this.limit) throw new Error('JSON frame size limit exceeded');
      const line = this.buffer.slice(0, end).trim();
      this.buffer = this.buffer.slice(end + 1);
      if (line) frames.push(JSON.parse(line));
    }
    if (this.buffer.length > this.limit) throw new Error('JSON frame size limit exceeded');
    return frames;
  }
}
