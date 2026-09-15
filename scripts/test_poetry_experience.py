import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from poetry_experience import Client, MODEL


class ExperienceTests(unittest.TestCase):
    def test_model_usage_and_secret_not_logged(self):
        response = {'model': MODEL, 'choices': [{'message': {'content': '{"ok":true}'}, 'finish_reason': 'stop'}],
                    'usage': {'total_tokens': 12}}
        with tempfile.TemporaryDirectory() as temporary:
            client = Client('secret-test-key', Path(temporary), max_calls=1)
            with patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(response).encode())) as call:
                self.assertEqual(client.ask('test', 'JSON', {}), '{"ok":true}')
                body = json.loads(call.call_args.args[0].data)
                self.assertEqual(body['model'], 'deepseek-flash')
            self.assertEqual(client.tokens, 12)
            self.assertNotIn('secret-test-key', (Path(temporary)/'api-001.json').read_text(encoding='utf-8'))
            with self.assertRaises(ValueError):
                client.ask('over budget', 'JSON', {})


if __name__ == '__main__':
    unittest.main()
