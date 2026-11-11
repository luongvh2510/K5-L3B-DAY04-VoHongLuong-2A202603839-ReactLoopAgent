"""Tests phần bonus của agent: giới hạn retry 429 của tool, Retry-After, lược context.

Không gọi LLM API; time.sleep được patch nên không chờ thật.
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent
from agent import run_agent
from bonus.tools import BonusTools
from status_tools import StatusTools
from test_lab import FakeProvider, tool_call, final


class RateLimitTools:
    """Tool giả: get_vercel_status luôn trả 429 với retry_after_seconds cho trước."""
    schemas, system_prompt = [], 'Test prompt.'
    def __init__(self, retry_after):
        self.retry_after, self.executed = retry_after, 0
    def execute(self, name, args):
        self.executed += 1
        return {'ok': False, 'error': 'RATE_LIMITED', 'http_status': 429,
                'retry_after_seconds': self.retry_after, 'retriable': True}


def vercel(call_id):
    return tool_call(call_id, 'get_vercel_status', '{}')


@patch('agent.time.sleep')
class BonusAgentExtraTests(unittest.TestCase):
    def test_persistent_429_is_capped_at_three_real_calls(self, sleep):
        tools = BonusTools(scenario='rate_limit_always')
        fake = FakeProvider([vercel('1'), vercel('2'), vercel('3'), vercel('4'), final('Vercel: không xác định')])
        result = run_agent('Kiểm tra Vercel', fake, tools, max_steps=6)
        self.assertEqual(result['status'], 'completed')
        # Chỉ 3 lần thực sự gọi API; lần thứ 4 bị agent chặn.
        self.assertEqual(tools.calls[('get_vercel_status', 'vercel')], agent.TOOL_MAX_ATTEMPTS)
        tool_events = [t for t in result['trace'] if t['event'] == 'tool']
        self.assertEqual(len(tool_events), agent.TOOL_MAX_ATTEMPTS)
        self.assertEqual([t['result']['attempts_left'] for t in tool_events], [2, 1, 0])
        blocked = [t for t in result['trace'] if t['event'] == 'tool_blocked']
        self.assertEqual(len(blocked), 1)
        self.assertEqual(json.loads(fake.histories[4][-1]['content'])['error'], 'RETRY_LIMIT_REACHED')

    def test_retry_after_is_respected_when_short(self, sleep):
        tools = RateLimitTools(retry_after=2)
        fake = FakeProvider([vercel('1'), final()])
        result = run_agent('x', fake, tools)
        sleep.assert_called_once_with(2.0)
        self.assertTrue(any(t['event'] == 'rate_limit_wait' for t in result['trace']))

    def test_retry_after_too_long_stops_retry_without_waiting(self, sleep):
        tools = RateLimitTools(retry_after=agent.RETRY_AFTER_CAP + 50)
        fake = FakeProvider([vercel('1'), vercel('2'), final()])
        result = run_agent('x', fake, tools)
        sleep.assert_not_called()
        self.assertEqual(tools.executed, 1)                     # lần 2 bị chặn, không gọi API
        self.assertEqual(result['trace'][1]['result']['attempts_left'], 0)
        self.assertTrue(any(t['event'] == 'tool_blocked' for t in result['trace']))

    def test_noisy_list_is_compacted_before_model_sees_it(self, sleep):
        tools = BonusTools(scenario='noisy')
        fake = FakeProvider([vercel('1'), final()])
        run_agent('x', fake, tools)
        seen = json.loads(fake.histories[1][-1]['content'])
        self.assertEqual(len(seen['distractors']), agent.LIST_KEEP)
        self.assertIn('160', seen['agent_context_note'])
        self.assertEqual(seen['provider'], 'vercel')            # dữ liệu chính vẫn còn

    def test_main_lab_prompt_and_results_unchanged(self, sleep):
        fake = FakeProvider([tool_call('1', 'get_status', '{"provider":"github"}'), final()])
        run_agent('x', fake, StatusTools())
        self.assertEqual(fake.histories[0][0]['content'], agent.SYSTEM_PROMPT)
        self.assertNotIn('agent_context_note', fake.histories[1][-1]['content'])

    def test_bonus_prompt_gets_agent_rules(self, sleep):
        fake = FakeProvider([final()])
        run_agent('x', fake, BonusTools())
        system = fake.histories[0][0]['content']
        self.assertTrue(system.startswith(BonusTools.system_prompt))
        self.assertIn('RETRY_LIMIT_REACHED', system)


if __name__ == '__main__':
    unittest.main()
