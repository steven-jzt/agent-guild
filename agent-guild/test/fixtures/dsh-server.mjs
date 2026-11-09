import { createInterface } from 'node:readline';
const send = frame => process.stdout.write(JSON.stringify(frame) + '\n');
let sessionId;
let waiting;
let answer;
function finish() {
  if (waiting === undefined || answer === undefined) return;
  send({ method: 'session.event', params: { sessionId, event: { type: 'answer-recorded', data: answer } } });
  send({ id: waiting, result: {} });
}
createInterface({ input: process.stdin }).on('line', line => {
  const request = JSON.parse(line);
  if (request.method === 'initialize') send({ id: request.id, result: { serverInfo: { name: 'test-runtime', version: 'test' } } });
  if (request.method === 'session/prompt') {
    sessionId = request.params.sessionId;
    send({ id: request.id, result: { messageId: 'queued-message' } });
    send({ id: 'question', method: 'user-question/ask', params: { sessionId, questions: [{ id: 'choice' }] } });
  }
  if (request.method === 'session/wait') { waiting = request.id; finish(); }
  if (request.id === 'question') {
    answer = request.result; finish();
  }
  if (request.method === 'shutdown') { send({ id: request.id, result: {} }); process.exit(0); }
}).on('close', () => process.exit(0));
