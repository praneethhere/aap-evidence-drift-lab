import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import routing_ab as m
from jinja2 import Environment, StrictUndefined

class StrictClassifierTests(unittest.TestCase):
    def render(self, message, failed_task='Attempt stale version-bound merge patch'):
        env=Environment(undefined=StrictUndefined)
        env.filters['regex_replace']=lambda val, pattern, repl: re.sub(pattern,repl,val)
        expr=m.strict_conflict_expression()
        result=env.from_string('{{ ('+expr+') | string }}').render(
            ansible_failed_result={'msg':message},
            ansible_failed_task={'name':failed_task},
        )
        return result=='True'

    def test_multiline_json_conflict_is_classified(self):
        msg='Failed to patch object: {\n  "code": 409,\n  "reason": "Conflict"\n}'
        self.assertTrue(self.render(msg))

    def test_tabs_and_whitespace_in_nested_json(self):
        msg='Failed to patch object:\n{\n"code" : \t409, "reason": \n"Conflict"}'
        self.assertTrue(self.render(msg))

    def test_404_not_conflict(self):
        self.assertFalse(self.render('Failed to patch object: {"code": 404, "reason": "NotFound"}'))

    def test_patch_success_guard_cannot_be_classified(self):
        self.assertFalse(self.render('UNEXPECTED_PATCH_SUCCESS','Stop on unexpected patch success'))

    def test_other_task_cannot_be_classified_even_if_message_mentions_409(self):
        msg='Failed to patch object: {"code":409,"reason":"Conflict"}'
        self.assertFalse(self.render(msg,'Some other task'))

    def test_text_without_module_prefix_rejected(self):
        self.assertFalse(self.render('{"code":409,"reason":"Conflict"}'))

    def test_generated_stale_play_uses_strict_expression_and_no_log(self):
        play=m.make_play('isolated-ns','patch-stale','3','patch',True)
        block=play[0]['tasks'][0]
        self.assertTrue(block['block'][0]['no_log'])
        self.assertTrue(block['rescue'][0]['no_log'])
        self.assertTrue(block['rescue'][1]['no_log'])
        expr=block['rescue'][0]['ansible.builtin.set_fact']['native_conflict']
        self.assertIn('regex_replace',expr)
        self.assertIn('ansible_failed_task.name',expr)
        self.assertIn('"code":409',expr)
        self.assertIn('"reason":"Conflict"',expr)
        self.assertIn('Failed to patch object:',expr)

if __name__=='__main__': unittest.main()
