import { RpcProcess, isRecord, type RpcFrame } from '../transport/rpc-process.ts';
import { dshFailure } from './dsh-outcome.ts';

// Uses the official SDK stdio protocol; the published 0.0.1 client lacks host-question callbacks.
export class DshAdapter {
  readonly rpc: RpcProcess;
  constructor(command: string, args: string[], cwd: string, env = process.env, answer?: (method: string, params: unknown) => Promise<unknown>) {
    this.rpc = new RpcProcess(command, args, cwd, env);
    this.rpc.on('request', (frame: RpcFrame) => {
      const fallback = frame.method === 'approval/request' ? { outcome: 'rejected' } : { cancelled: true };
      Promise.resolve().then(() => answer ? answer(frame.method!, frame.params) : fallback).then(result => {
        try { this.rpc.respond(frame.id!, result); } catch { /* request or transport was closed */ }
      }).catch(() => { try { this.rpc.respond(frame.id!, fallback); } catch { /* fail closed */ } });
    });
  }
  async initialize(cwd: string, provider: string, model: string): Promise<void> {
    const result = await this.rpc.request('initialize', { cwd, provider, model, maxTokens: 8192, approvalRequests: true, capabilities: { userQuestions: true } });
    if (!isRecord(result) || !isRecord(result.serverInfo)) throw new Error('DSH initialization did not return server identity');
  }
  async run(sessionId: string, prompt: string): Promise<unknown[]> {
    const events: unknown[] = [];
    const observe = (frame: RpcFrame) => { if (isRecord(frame.params) && frame.params.sessionId === sessionId) events.push(frame); };
    this.rpc.on('notification', observe);
    try {
      const receipt = await this.rpc.request('session/prompt', { sessionId, contentBlocks: [{ type: 'text', text: prompt }] });
      if (!isRecord(receipt) || typeof receipt.messageId !== 'string') throw new Error('DSH did not acknowledge prompt identity');
      await this.rpc.request('session/wait', { sessionId }, 180000);
      const failure = dshFailure(events, sessionId);
      if (failure) throw failure;
      return events;
    } finally { this.rpc.off('notification', observe); }
  }
  async close(): Promise<void> {
    try { await this.rpc.request('shutdown', {}, 1000); } catch { /* transport may already be closed */ }
    await this.rpc.close();
  }
}
