import { EventEmitter } from 'node:events';
import { managedSpawn } from './managed-process.ts';
import { JsonLines } from './json-lines.ts';

type Pending = { resolve: (result: unknown) => void; reject: (error: Error) => void; timer: NodeJS.Timeout };
export type RpcFrame = { id?: number | string; method?: string; params?: unknown; result?: unknown; error?: unknown };

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export class RpcProcess extends EventEmitter {
  readonly child;
  private serial = 0;
  private closed = false;
  private pending = new Map<string | number, Pending>();
  private inbound = new Set<string | number>();
  private exited: Promise<void>;
  private stderr = '';
  private closing?: Promise<void>;

  constructor(executable: string, args: string[], cwd: string, env = process.env) {
    super(); this.child = managedSpawn(executable, args, { cwd, env });
    const lines = new JsonLines();
    this.child.stdout.setEncoding('utf8');
    this.child.stdout.on('data', (chunk: string) => {
      try { for (const frame of lines.push(chunk)) this.receive(frame); }
      catch (error) { this.fail(error instanceof Error ? error : new Error(String(error))); this.child.kill(); }
    });
    this.child.stderr.setEncoding('utf8');
    this.child.stderr.on('data', (chunk: string) => { this.stderr = (this.stderr + chunk).slice(-12000); });
    this.child.stdin.on('error', error => this.fail(error));
    this.exited = new Promise(resolve => {
      this.child.once('error', error => { this.fail(error); resolve(); });
      this.child.once('close', code => { this.fail(new Error(`Transport exited (${code})`)); resolve(); });
    });
  }

  private receive(value: unknown): void {
    if (!isRecord(value)) throw new Error('Invalid JSON-RPC frame');
    if (value.id !== undefined && typeof value.id !== 'string' && !(typeof value.id === 'number' && Number.isFinite(value.id))) throw new Error('Invalid JSON-RPC identity');
    const frame = value as RpcFrame;
    if (typeof frame.method === 'string') {
      if (frame.id !== undefined) {
        this.inbound.add(frame.id);
        if (this.listenerCount('request')) this.emit('request', frame);
        else { this.inbound.delete(frame.id); this.send({ id: frame.id, error: { code: -32601, message: 'No request handler registered' } }); }
      } else this.emit('notification', frame);
      return;
    }
    if (frame.id === undefined) throw new Error('JSON-RPC response has no identity');
    if (('result' in frame) === ('error' in frame)) throw new Error('Invalid JSON-RPC response');
    const pending = this.pending.get(frame.id);
    if (!pending) return; // A response to a timed-out request must not re-run or complete new work.
    this.pending.delete(frame.id); clearTimeout(pending.timer);
    if (frame.error !== undefined) pending.reject(new Error(JSON.stringify(frame.error)));
    else pending.resolve(frame.result);
  }

  private send(frame: RpcFrame): void {
    if (this.closed) throw new Error('Transport closed');
    this.child.stdin.write(JSON.stringify({ jsonrpc: '2.0', ...frame }) + '\n');
  }

  async request(method: string, params: unknown = {}, timeoutMs = 30000): Promise<unknown> {
    if (this.closed) throw new Error('Transport closed');
    const id = ++this.serial;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`${method} timed out; execution state is unknown`)); }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      try { this.send({ id, method, params }); }
      catch (error) { clearTimeout(timer); this.pending.delete(id); reject(error); }
    });
  }

  notify(method: string, params: unknown = {}): void { this.send({ method, params }); }
  respond(id: string | number, result: unknown): void {
    if (!this.inbound.delete(id)) throw new Error('Question is closed or already answered');
    this.send({ id, result });
  }
  diagnostics(): string { return this.stderr; }

  private fail(error: Error): void {
    this.closed = true;
    for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(error); }
    this.pending.clear(); this.inbound.clear(); this.emit('failure', error);
  }

  close(): Promise<void> {
    this.closing ??= this.performClose();
    return this.closing;
  }

  private async performClose(): Promise<void> {
    this.fail(new Error('Transport closed'));
    this.child.stdin.end();
    let timer: NodeJS.Timeout | undefined;
    const graceful = await Promise.race([this.exited.then(() => true), new Promise<false>(resolve => { timer = setTimeout(() => resolve(false), 1500); })]);
    clearTimeout(timer);
    if (!graceful) this.child.kill();
    const stopped = await Promise.race([this.exited.then(() => true), new Promise<false>(resolve => { timer = setTimeout(() => resolve(false), 5000); })]);
    clearTimeout(timer);
    if (!stopped) throw new Error('Process shutdown could not be confirmed');
  }
}
