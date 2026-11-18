"""Explicit OFFLINE fixture. Launched only for registered isolated fixture projects."""
import json
from pathlib import Path
import sys
import time


def send(frame):
    print(json.dumps(frame), flush=True)


def main():
    scenario = sys.argv[1]
    for line in sys.stdin:
        frame = json.loads(line)
        method, mid = frame.get('method'), frame.get('id')
        if method == 'initialize':
            result = {'serverInfo': {'name': 'OFFLINE REVIEW FIXTURE', 'version': '1'}}
        elif method == 'thread/start':
            if frame['params']['sandbox'] != 'read-only':
                raise RuntimeError('fixture requires read-only review request')
            result = {'thread': {'id': 'fixture-thread'}}
        elif method == 'turn/start':
            prompt = frame['params']['input'][0]['text']
            context = json.loads(prompt.split('GUILD_REVIEW_CONTEXT\n', 1)[1])
            send({'jsonrpc': '2.0', 'id': mid, 'result': {'turn': {'id': 'fixture-turn', 'status': 'inProgress'}}})
            if scenario == 'timeout':
                continue
            if scenario == 'tamper':
                Path('src/a.py').write_text('tampered\n')
            report = {'candidate_sha': context['candidate_sha'], 'batch_id': context['batch_id'],
                      'decision': 'request_changes' if scenario == 'defect' else 'pass',
                      'summary': 'OFFLINE simulated review', 'findings': []}
            if scenario in ('defect', 'advice', 'contradictory'):
                report['findings'] = [{'severity': 'P3' if scenario == 'advice' else 'P2',
                                       'path': 'src/a.py', 'line': 1, 'message': 'OFFLINE controlled finding'}]
            if scenario == 'wrong-sha':
                report['candidate_sha'] = '0' * 40
            if scenario == 'wrong-batch':
                report['batch_id'] = 'wrong-batch'
            if scenario == 'secret':
                report['summary'] = 'Bearer fixture-secret-value sk-0123456789abcdef'
            text = 'not JSON' if scenario == 'invalid' else json.dumps(report)
            send({'jsonrpc': '2.0', 'method': 'item/completed', 'params': {
                'threadId': 'fixture-thread', 'turnId': 'wrong-turn' if scenario == 'wrong-turn' else 'fixture-turn',
                'item': {'id': 'fixture-answer', 'type': 'agentMessage', 'text': text}}})
            status = 'failed' if scenario == 'failed' else 'interrupted' if scenario == 'interrupted' else 'completed'
            send({'jsonrpc': '2.0', 'method': 'turn/completed', 'params': {
                'threadId': 'fixture-thread', 'turn': {'id': 'fixture-turn', 'status': status, 'items': []}}})
            continue
        elif method == 'turn/interrupt':
            result = {}
        else:
            continue
        send({'jsonrpc': '2.0', 'id': mid, 'result': result})


if __name__ == '__main__':
    main()
