import json
import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import rpc_adapter


class Router:
    def __init__(self):
        self.calls = []
        self.usage_records = []
        self.mode = 'auto'
        self.model = 'gpt-6-luna'

    def _catalog(self):
        return {'models': [{'slug': 'gpt-6-sol', 'visibility': 'list', 'supported_reasoning_levels': [{'effort': 'medium'}]}]}

    def _config(self):
        return {'mode': self.mode}

    def decide(self, payload, **kwargs):
        self.calls.append((payload, kwargs))
        return {'model': self.model, 'effort': 'low', 'mode': 'auto', 'reason': 'jev'}

    def record_usage(self, *args, **kwargs):
        self.usage_records.append((args, kwargs))


def request(method, params, rid=1):
    return (json.dumps({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params}) + '\n').encode()


def response(rid, result):
    return (json.dumps({'jsonrpc': '2.0', 'id': rid, 'result': result}) + '\n').encode()


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.router = Router()
        self.adapter = rpc_adapter.Adapter(self.root, router=self.router)

    def tearDown(self):
        self.tmp.cleanup()

    def test_auto_astra_proposal_is_clamped_but_concrete_astra_passes(self):
        self.router.model = 'gpt-6-astra'
        routed = json.loads(self.adapter.client(request('turn/start', {'threadId': 'auto', 'model': 'jev-auto',
            'input': [{'type': 'text', 'text': 'Plan an architecture change'}]})))
        self.assertEqual(routed['params']['model'], 'gpt-6-sol')
        manual = json.loads(self.adapter.client(request('turn/start', {'threadId': 'manual', 'model': 'gpt-6-astra',
            'input': [{'type': 'text', 'text': 'Plan an architecture change'}]}, 2)))
        self.assertEqual(manual['params']['model'], 'gpt-6-astra')

    def test_astra_allowlist_routes_without_dialog(self):
        self.router._config = lambda: {'mode': 'auto', 'auto_roles': ['luna', 'terra', 'sol', 'astra']}
        self.router.model = 'gpt-6-astra'
        routed = json.loads(self.adapter.client(request('turn/start', {'threadId': 'allowed', 'model': 'jev-auto',
            'input': [{'type': 'text', 'text': 'Review architecture'}]})))
        self.assertEqual(routed['params']['model'], 'gpt-6-astra')

    def test_passthrough(self):
        for raw in (b'{ "jsonrpc":"2.0", "method":"other", "params":{"secret":"x"} }\n',
                    b'{"jsonrpc":"2.0","id":55,"method":"approval","params":{"foo":1}}\n',
                    b'{"jsonrpc":"2.0","id":7,"error":{"code":-1,"message":"opaque"}}\n',
                    b'bad json\n', b'[1,2]\n'):
            self.assertEqual(self.adapter.client(raw), raw)
            self.assertEqual(self.adapter.server(raw), raw)
        self.assertFalse(self.router.calls)

    def test_start_turn_usage_and_privacy(self):
        sent = json.loads(self.adapter.client(request('thread/start', {'model': 'jev-auto', 'cwd': '/tmp'})))
        self.assertEqual(sent['params'], {'model': 'gpt-6-sol', 'cwd': '/tmp'})
        returned = json.loads(self.adapter.server(response(1, {'thread': {'id': 'thread-1', 'model': 'gpt-6-sol'}, 'model': 'gpt-6-sol'})))
        self.assertEqual(returned['result']['model'], 'jev-auto')
        self.assertEqual(returned['result']['thread']['model'], 'jev-auto')
        input_items = [{'type': 'text', 'text': 'Fix a simple typo'}, {'type': 'image', 'data': 'secret-image'}]
        sent = json.loads(self.adapter.client(request('turn/start', {'threadId': 'thread-1', 'model': 'jev-auto', 'input': input_items}, 2)))
        self.assertEqual((sent['params']['model'], sent['params']['effort']), ('gpt-6-luna', 'low'))
        self.assertEqual(sent['params']['input'], input_items)
        self.assertEqual(self.router.calls[0][1], {'client': 'desktop', 'session_id': 'thread-1', 'native_selection': True, 'mode_override': 'auto'})
        self.assertNotIn('secret-image', json.dumps(self.router.calls[0][0]))
        usage = {'jsonrpc': '2.0', 'method': 'thread/tokenUsage/updated', 'params': {'threadId': 'thread-1', 'tokenUsage': {'last': {'inputTokens': 9000}}}}
        self.adapter.server((json.dumps(usage) + '\n').encode())
        state = self.root / 'state/desktop-intent.json'
        self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o600)
        self.assertNotIn('thread-1', state.read_text())
        self.assertNotIn('typo', state.read_text())
        self.assertEqual(self.adapter.store.get('thread-1')['context'], 9000)
        cached_usage = {'jsonrpc': '2.0', 'method': 'thread/tokenUsage/updated', 'params': {
            'threadId': 'thread-1', 'tokenUsage': {'last': {'inputTokens': 10000,
                                                          'cachedInputTokens': 2500, 'outputTokens': 100}}}}
        self.adapter.server((json.dumps(cached_usage) + '\n').encode())
        self.adapter._route('jev-auto', {'input': [{'type': 'text', 'text': 'Fix the next typo'}]},
                            'thread-1', self.adapter.store.get('thread-1'))
        self.assertEqual(self.router.calls[-1][0]['cached_input_pct'], 25)
        settings = {'jsonrpc': '2.0', 'method': 'thread/settings/updated', 'params': {'threadId': 'thread-1', 'threadSettings': {'model': 'gpt-6-luna', 'effort': 'low'}}}
        self.assertEqual(json.loads(self.adapter.server((json.dumps(settings)+'\n').encode()))['params']['threadSettings']['model'], 'jev-auto')

    def test_thread_metadata_keeps_alias_for_desktop_effort_picker(self):
        self.adapter.store.update('t', alias='jev-auto', actual='gpt-6-sol', effort='medium')
        started = {'jsonrpc': '2.0', 'method': 'thread/started', 'params': {'thread': {'id': 't', 'model': 'gpt-6-sol'}}}
        self.assertEqual(json.loads(self.adapter.server((json.dumps(started)+'\n').encode()))['params']['thread']['model'], 'jev-auto')
        self.assertEqual(self.adapter.client(request('thread/read', {'threadId': 't'}, 5)), request('thread/read', {'threadId': 't'}, 5))
        read = json.loads(self.adapter.server(response(5, {'thread': {'id': 't', 'model': 'gpt-6-sol'}})))
        self.assertEqual(read['result']['thread']['model'], 'jev-auto')
        self.adapter.client(request('thread/list', {'limit': 10}, 6))
        listed = json.loads(self.adapter.server(response(6, {'data': [{'id': 't', 'model': 'gpt-6-sol'},
                                                                        {'id': 'manual', 'model': 'gpt-6-astra'}]})))
        self.assertEqual([x['model'] for x in listed['result']['data']], ['jev-auto', 'gpt-6-astra'])
        settings = {'jsonrpc': '2.0', 'method': 'thread/settings/updated', 'params': {
            'threadId': 't', 'threadSettings': {'model': 'gpt-6-sol', 'effort': 'high',
            'collaborationMode': {'settings': {'model': 'gpt-6-sol', 'reasoning_effort': 'high'}}}}}
        returned = json.loads(self.adapter.server((json.dumps(settings)+'\n').encode()))['params']['threadSettings']
        self.assertEqual(returned['model'], 'jev-auto')
        self.assertEqual(returned['collaborationMode']['settings']['model'], 'jev-auto')
        self.adapter.store.update('t', clear=True)
        self.adapter.client(request('thread/read', {'threadId': 't'}, 7))
        manual = json.loads(self.adapter.server(response(7, {'thread': {'id': 't', 'model': 'gpt-6-sol'}})))
        self.assertEqual(manual['result']['thread']['model'], 'gpt-6-sol')

    def test_auto_ignores_picker_effort_and_clears_legacy_override(self):
        self.router._catalog = lambda: {'models': [
            {'slug': 'gpt-6-luna', 'visibility': 'list', 'supported_reasoning_levels':
             [{'effort': x} for x in ('low', 'medium', 'high', 'max')]},
            {'slug': 'gpt-6-sol', 'visibility': 'list', 'supported_reasoning_levels':
             [{'effort': x} for x in ('low', 'medium', 'high', 'max', 'ultra')]}]}
        self.adapter.store.update('t', alias='jev-auto', actual='gpt-6-sol', effort='medium', effort_override='max')
        changed = json.loads(self.adapter.client(request('thread/settings/update', {
            'threadId': 't', 'model': 'jev-auto', 'effort': 'high'}, 11)))
        self.assertEqual((changed['params']['model'], changed['params']['effort']), ('gpt-6-sol', 'high'))
        self.assertNotIn('effort_override', self.adapter.store.get('t'))
        turn = json.loads(self.adapter.client(request('turn/start', {'threadId': 't', 'model': 'jev-auto',
            'input': [{'type': 'text', 'text': 'simple task'}]}, 12)))
        self.assertEqual((turn['params']['model'], turn['params']['effort']), ('gpt-6-luna', 'low'))
        self.assertNotIn('requested_effort', self.router.calls[-1][0])
        self.adapter.active.discard('t')
        self.adapter.client(request('thread/settings/update', {'threadId': 't', 'model': 'jev-auto', 'effort': 'ultra'}, 13))
        turn = json.loads(self.adapter.client(request('turn/start', {'threadId': 't', 'model': 'jev-auto',
            'input': [{'type': 'text', 'text': 'simple task'}]}, 14)))
        self.assertEqual((turn['params']['model'], turn['params']['effort']), ('gpt-6-luna', 'low'))
        self.assertNotIn('requested_effort', self.router.calls[-1][0])
        self.adapter.client(request('thread/settings/update', {'threadId': 't', 'model': 'jev-auto', 'effort': None}, 15))
        self.assertNotIn('effort_override', self.adapter.store.get('t'))

    def test_initial_nondefault_effort_does_not_override_auto(self):
        raw = request('thread/start', {'model': 'jev-auto', 'config': {'model_reasoning_effort': 'high'}}, 10)
        self.adapter.client(raw)
        self.adapter.server(response(10, {'thread': {'id': 't', 'model': 'gpt-6-sol'},
                                          'model': 'gpt-6-sol', 'reasoningEffort': 'high'}))
        self.assertNotIn('effort_override', self.adapter.store.get('t'))

    def test_auto_receives_last_concrete_model_for_cache_continuity(self):
        self.adapter.client(request('turn/start', {'threadId': 't', 'model': 'gpt-6-sol',
            'effort': 'max', 'input': [{'type': 'text', 'text': 'Manual work'}]}, 20))
        self.adapter.client(request('turn/start', {'threadId': 't', 'model': 'jev-auto',
            'input': [{'type': 'text', 'text': 'Continue'}]}, 21))
        self.assertEqual(self.router.calls[-1][0]['current_model'], 'gpt-6-sol')
        self.assertNotIn('requested_effort', self.router.calls[-1][0])

    def test_manual_and_collaboration_precedence(self):
        self.adapter.store.update('t', alias='jev-auto')
        raw = request('turn/start', {'threadId': 't', 'model': 'gpt-6-astra', 'input': []})
        self.assertEqual(self.adapter.client(raw), raw)
        self.assertIsNone(self.adapter.store.get('t'))
        self.adapter.store.update('t', alias='jev-auto')
        raw = request('turn/start', {'threadId': 't', 'model': 'jev-auto', 'collaborationMode': {'mode': 'plan', 'settings': {'model': 'gpt-6-astra', 'reasoning_effort': 'high'}}, 'input': []})
        sent = json.loads(self.adapter.client(raw))
        self.assertEqual(sent['params']['model'], 'gpt-6-astra')
        self.assertIsNone(self.adapter.store.get('t'))
        self.adapter.store.update('t', alias='jev-auto')
        raw = request('turn/start', {'threadId': 't', 'model': 'jev-auto', 'collaborationMode': {'mode': 'plan', 'settings': {'model': 'jev-auto', 'developerInstructions': 'keep'}}, 'input': [{'type': 'text', 'text': 'test'}]})
        sent = json.loads(self.adapter.client(raw))
        self.assertEqual(sent['params']['collaborationMode']['settings']['model'], 'gpt-6-luna')
        self.assertEqual(sent['params']['collaborationMode']['settings']['reasoning_effort'], 'low')
        self.assertEqual(sent['params']['collaborationMode']['settings']['developerInstructions'], 'keep')

    def test_native_usage_notification_is_recorded_without_prompt(self):
        self.adapter.client(request('turn/start', {'threadId': 't', 'model': 'gpt-6-sol', 'effort': 'medium',
                                                   'input': [{'type': 'text', 'text': 'private prompt'}]}))
        usage = {'jsonrpc': '2.0', 'method': 'thread/tokenUsage/updated', 'params': {
            'threadId': 't', 'turnId': 'turn-1', 'tokenUsage': {'last': {
                'inputTokens': 100, 'cachedInputTokens': 40, 'outputTokens': 7}}}}
        self.adapter.server((json.dumps(usage) + '\n').encode())
        complete = {'jsonrpc': '2.0', 'method': 'turn/completed', 'params': {
            'threadId': 't', 'turn': {'id': 'turn-1', 'status': 'completed'}}}
        self.adapter.server((json.dumps(complete) + '\n').encode())
        (decision, observed, status), _ = self.router.usage_records[-1]
        self.assertEqual((decision['model'], decision['effort'], status), ('gpt-6-sol', 'medium', 'ok'))
        self.assertEqual(observed['input_tokens'], 100)
        self.assertEqual(observed['input_tokens_details']['cached_tokens'], 40)
        self.assertNotIn('private prompt', json.dumps((decision, observed)))

    def test_resume_persistence_unknown_context_and_fork(self):
        self.adapter.store.update('t', alias='jev-auto', actual='gpt-6-astra', effort='high', conservative=True)
        other = rpc_adapter.Adapter(self.root, router=self.router)
        self.assertEqual(json.loads(other.client(request('thread/resume', {'threadId': 't', 'model': 'jev-auto'})))['params']['model'], 'gpt-6-sol')
        other.server(response(1, {'thread': {'id': 't'}, 'model': 'gpt-6-astra'}))
        sent = json.loads(other.client(request('turn/start', {'threadId': 't', 'input': [{'type': 'text', 'text': 'continue'}]})))
        self.assertEqual(sent['params']['model'], 'gpt-6-sol')
        other.store.update('unknown', alias='jev-auto')
        unknown_resume = json.loads(other.client(request('thread/resume', {'threadId': 'unknown', 'model': 'jev-auto'}, 3)))
        self.assertNotIn('model', unknown_resume['params'])
        other.server(response(3, {'thread': {'id': 'unknown'}, 'model': 'gpt-6-sol'}))
        sent = json.loads(other.client(request('turn/start', {'threadId': 'unknown', 'input': [{'type': 'text', 'text': 'continue'}]}, 4)))
        self.assertEqual(sent['params']['model'], 'gpt-6-sol')
        other.client(request('thread/fork', {'threadId': 'unknown'}, 5))
        other.server(response(5, {'thread': {'id': 'forked'}, 'model': 'gpt-6-sol'}))
        self.assertEqual(other.store.get('forked')['alias'], 'jev-auto')

    def test_steer_failure_kill_switch_custom_provider(self):
        self.adapter.store.update('t', alias='jev-shadow')
        raw = request('turn/start', {'threadId': 't', 'model': 'jev-shadow', 'input': [{'type': 'text', 'text': 'hello'}]})
        self.adapter.client(raw)
        count = len(self.router.calls)
        steer = request('turn/steer', {'threadId': 't', 'input': []}, 3)
        self.assertEqual(self.adapter.client(steer), steer)
        self.assertEqual(len(self.router.calls), count)
        completed = {'jsonrpc': '2.0', 'method': 'turn/completed', 'params': {'threadId': 't', 'turn': {'status': 'failed'}}}
        self.adapter.server((json.dumps(completed)+'\n').encode())
        self.adapter.client(raw)
        self.assertIn('Previous turn failure', json.dumps(self.router.calls[-1][0]))
        self.router.mode = 'off'
        count = len(self.router.calls)
        self.adapter.client(raw)
        self.assertEqual(len(self.router.calls), count)
        custom = request('thread/start', {'model': 'jev-auto', 'modelProvider': 'ollama'})
        self.assertEqual(self.adapter.client(custom), custom)
        configured = request('thread/start', {'model': 'jev-auto', 'config': {'model': 'gpt-6-astra'}})
        self.assertEqual(json.loads(self.adapter.client(configured))['params']['model'], 'gpt-6-astra')

    def test_malformed_model_and_start_error_do_not_poison_active(self):
        raw = request('turn/start', {'threadId': 't', 'model': ['jev-auto'], 'input': []})
        self.assertEqual(self.adapter.client(raw), raw)
        self.adapter.store.update('t', alias='jev-auto')
        self.adapter.client(request('turn/start', {'threadId': 't', 'model': 'jev-auto', 'input': []}, 2))
        self.assertIn('t', self.adapter.active)
        error = b'{"jsonrpc":"2.0","id":2,"error":{"code":-1,"message":"opaque"}}\n'
        self.assertEqual(self.adapter.server(error), error)
        self.assertNotIn('t', self.adapter.active)

    def test_real_router_failure_floor(self):
        from core import Router
        catalog = {'models': [{'slug': slug, 'visibility': 'list', 'supported_reasoning_levels': [{'effort': 'medium'}]}
                              for slug in ('gpt-6-luna', 'gpt-6-sol')]}
        (self.root / 'native-models.json').write_text(json.dumps(catalog))
        (self.root / 'config.json').write_text(json.dumps({'mode': 'auto'}))
        real = Router(self.root / 'config.json', self.root / 'native-models.json',
                      self.root / 'state/leases.json', self.root / 'state/telemetry.jsonl',
                      jev_client=lambda *args: {'answers': {'capability': {'choice': 'luna'}, 'effort': {'choice': 'medium'}}})
        adapter = rpc_adapter.Adapter(self.root, router=real)
        adapter.store.update('t', alias='jev-auto', failed=True)
        sent = json.loads(adapter.client(request('turn/start', {'threadId': 't', 'model': 'jev-auto',
                                                                'input': [{'type': 'text', 'text': 'Continue please'}]})))
        self.assertEqual(sent['params']['model'], 'gpt-6-sol')

    def test_stdio_pump_with_fake_native_and_non_app_server_exec(self):
        native = self.root / 'fake-native'
        native.write_text('''#!%s
import json, sys
if sys.argv[1:2] != ['app-server'] and not (sys.argv[1:2] == ['-c'] and 'app-server' in sys.argv[1:]):
    print(json.dumps(sys.argv[1:]))
else:
    line = sys.stdin.readline()
    request = json.loads(line)
    print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':{'thread':{'id':'native-thread'},'model':request['params']['model'],'seenModel':request['params']['model']}}), flush=True)
    print(json.dumps({'jsonrpc':'2.0','id':99,'method':'item/approval','params':{'opaque':'pass'}}), flush=True)
    print(json.dumps({'jsonrpc':'2.0','method':'thread/settings/updated','params':{'threadId':'native-thread','threadSettings':{'model':request['params']['model'],'effort':'medium'}}}), flush=True)
''' % sys.executable)
        native.chmod(0o700)
        adapter_path = Path(rpc_adapter.__file__)
        args = [sys.executable, str(adapter_path), '--native', str(native), '--root', str(self.root), '--']
        result = subprocess.run(args + ['app-server', '--analytics-default-enabled'],
                                input=request('thread/start', {'model': 'jev-auto'}), capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        lines = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(lines[0]['result']['seenModel'], 'gpt-6-sol')
        self.assertEqual(lines[0]['result']['model'], 'jev-auto')
        self.assertEqual(lines[1]['method'], 'item/approval')
        self.assertEqual(lines[2]['params']['threadSettings']['model'], 'jev-auto')
        desktop = subprocess.run(args + ['-c', 'features.code_mode_host=true', 'app-server', '--analytics-default-enabled',
                                         '-c', 'plugins.codex-app-tools@openai-bundled.mcp_servers.codex_app.enabled=true'],
                                 input=request('thread/start', {'model': 'jev-auto'}), capture_output=True, timeout=5)
        self.assertEqual(desktop.returncode, 0, desktop.stderr.decode())
        self.assertEqual(json.loads(desktop.stdout.splitlines()[0])['result']['seenModel'], 'gpt-6-sol')
        bypass = subprocess.run(args + ['exec', 'app-server'], capture_output=True, timeout=5)
        self.assertEqual(json.loads(bypass.stdout), ['exec', 'app-server'])

    def test_relay_survives_transform_failure_and_keeps_next_frame(self):
        source = io.BytesIO(b'first\nsecond\n')
        target = io.BytesIO()
        def transform(raw):
            if raw == b'first\n':
                raise TypeError('sanitized')
            return raw.upper()
        rpc_adapter._relay(source, target, transform)
        self.assertEqual(target.getvalue(), b'first\nSECOND\n')


if __name__ == '__main__':
    unittest.main()
