"""Tests retry khi provider lỗi tạm thời; time.sleep được patch nên không chờ thật."""
import http.client
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent
from agent import run_agent
from status_tools import StatusTools
from test_lab import final


class FlakyProvider:
    """Ném lần lượt các lỗi trong `errors`, sau đó trả các response trong `responses`."""
    def __init__(self, errors, responses):
        self.errors, self.responses, self.calls = list(errors), iter(responses), 0
    def complete(self, messages, schemas):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return next(self.responses)


@patch('agent.time.sleep')
class RetryTests(unittest.TestCase):
    def test_transient_error_is_retried_then_completed(self, sleep):
        fake = FlakyProvider([http.client.RemoteDisconnected('closed')], [final('ok')])
        result = run_agent('x', fake, StatusTools(), max_steps=1)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(fake.calls, 2)
        retries = [t for t in result['trace'] if t['event'] == 'provider_retry']
        self.assertEqual([r['wait_s'] for r in retries], [agent.RETRY_DELAY])
        self.assertEqual(len(result['usage']), 1)   # lần lỗi không ghi usage

    def test_wait_increases_and_stops_after_max_retries(self, sleep):
        errors = [ConnectionResetError('reset')] * (agent.MAX_RETRIES + 1)
        fake = FlakyProvider(errors, [])
        result = run_agent('x', fake, StatusTools())
        self.assertEqual(result['status'], 'error')
        self.assertEqual(fake.calls, agent.MAX_RETRIES + 1)
        waits = [c.args[0] for c in sleep.call_args_list]
        self.assertEqual(waits, [agent.RETRY_DELAY * i for i in range(1, agent.MAX_RETRIES + 1)])
        self.assertEqual(result['trace'][-1]['event'], 'provider_error')

    def test_config_error_is_not_retried(self, sleep):
        fake = FlakyProvider([RuntimeError('Provider trả HTTP 401; kiểm tra model, key và quota.')], [])
        result = run_agent('x', fake, StatusTools())
        self.assertEqual(result['status'], 'error')
        self.assertEqual(fake.calls, 1)
        sleep.assert_not_called()

    def test_rate_limit_is_retried(self, sleep):
        fake = FlakyProvider([RuntimeError('Provider trả HTTP 429; kiểm tra model, key và quota.')], [final('ok')])
        self.assertEqual(run_agent('x', fake, StatusTools())['status'], 'completed')
        self.assertEqual(fake.calls, 2)


if __name__ == '__main__':
    unittest.main()
