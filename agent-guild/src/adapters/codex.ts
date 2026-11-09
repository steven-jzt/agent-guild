import { RpcProcess, isRecord, type RpcFrame } from '../transport/rpc-process.ts';

export class CodexAdapter {
  readonly rpc: RpcProcess;
  constructor(command: string, args: string[], cwd: string, env = process.env) { this.rpc = new RpcProcess(command, args, cwd, env); }
  async initialize(): Promise<void> {
    await this.rpc.request('initialize', { clientInfo: { name: 'agent_guild', title: 'Agent Guild M0', version: '0.1.0' } });
    this.rpc.notify('initialized');
  }

  async run(cwd: string, prompt: string, onEvent: (event: RpcFrame) => void = () => {}, timeoutMs = 180000): Promise<{ status: string; text: string }> {
    const opened = await this.rpc.request('thread/start', { cwd, approvalPolicy: 'on-request', sandbox: 'workspace-write', serviceName: 'agent-guild-m0' });
    if (!isRecord(opened) || !isRecord(opened.thread) || typeof opened.thread.id !== 'string') throw new Error('Codex did not return a thread identity');
    const threadId = opened.thread.id;
    let text = '';
    let timer: NodeJS.Timeout;
    let listener: (frame: RpcFrame) => void;
    let failure: (error: Error) => void;
    const completed = new Promise<{ status: string; text: string }>((resolve, reject) => {
      timer = setTimeout(() => reject(new Error('Codex turn timed out; stop required')), timeoutMs);
      listener = frame => {
        const params = frame.params;
        if (!isRecord(params) || params.threadId !== threadId) return;
        onEvent(frame);
        if (frame.method === 'item/completed' && isRecord(params.item) && params.item.type === 'agentMessage' && typeof params.item.text === 'string') text += params.item.text;
        if (frame.method === 'turn/completed' && isRecord(params.turn)) {
          const status = String(params.turn.status);
          if (status === 'completed') resolve({ status, text });
          else reject(new Error(`Codex turn ended: ${status}; ${JSON.stringify(params.turn.error ?? '')}`));
        }
      };
      failure = reject;
      this.rpc.on('notification', listener);
      this.rpc.on('failure', failure);
    });
    completed.catch(() => {}); // The turn may fail before its start acknowledgement arrives.
    try {
      await this.rpc.request('turn/start', { threadId, input: [{ type: 'text', text: prompt }] });
      return await completed;
    } finally {
      clearTimeout(timer!); this.rpc.off('notification', listener!); this.rpc.off('failure', failure!);
    }
  }
  close() { return this.rpc.close(); }
}
