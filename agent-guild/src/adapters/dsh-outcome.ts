import { isRecord } from '../transport/rpc-process.ts';

export function dshFailure(notifications: unknown[], sessionId: string): Error | undefined {
  for (const notification of [...notifications].reverse()) {
    if (!isRecord(notification) || !isRecord(notification.params) || notification.params.sessionId !== sessionId) continue;
    const event = notification.params.event;
    if (!isRecord(event) || event.type !== 'turn/end' || !isRecord(event.data) || !isRecord(event.data.reason)) continue;
    const reason = event.data.reason;
    if (reason.kind === 'error') {
      const error = reason.error;
      return new Error(isRecord(error) ? `${error.code ?? 'MODEL_ERROR'}: ${error.message ?? JSON.stringify(error)}` : String(error));
    }
    if (reason.kind === 'interrupted') return new Error('DSH turn interrupted');
    return undefined;
  }
  return undefined;
}
