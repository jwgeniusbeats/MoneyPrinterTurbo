import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services import llm  # noqa: E402

BODY = " ".join(["Your brain keeps a surprising amount of unfinished business in the background."] * 6)
CUT = BODY + " Understanding your capacity stops the crash, so follow for more quick"
GOOD = BODY + " Follow for more mind-bending psychology facts."


class ScriptCompletenessTests(unittest.TestCase):
    def test_cut_off_endings_are_detected_and_finished_ctas_are_not(self):
        self.assertFalse(llm.script_is_complete("... so follow for more quick"))
        self.assertFalse(llm.script_is_complete("... Because your brain can recall that nightmare so"))
        self.assertTrue(llm.script_is_complete("... follow for more mind hacks"))
        self.assertTrue(llm.script_is_complete("... follow for more psychology facts."))
        self.assertTrue(llm.script_is_complete('He said "stop."'))

    def test_a_cut_off_answer_is_retried_and_the_complete_one_is_used(self):
        with patch.object(llm, "_generate_response", side_effect=[CUT, GOOD]) as gen:
            script = llm.generate_script(video_subject="x")
        self.assertEqual(gen.call_count, 2)
        self.assertTrue(script.endswith("psychology facts."))

    def test_a_cta_without_final_period_is_accepted_and_closed(self):
        text = BODY + " Follow for more mind hacks"
        with patch.object(llm, "_generate_response", return_value=text) as gen:
            script = llm.generate_script(video_subject="x")
        self.assertEqual(gen.call_count, 1)
        self.assertTrue(script.endswith("mind hacks."))

    def test_when_every_attempt_is_cut_off_it_is_trimmed_to_the_last_full_sentence(self):
        with patch.object(llm, "_generate_response", return_value=CUT):
            script = llm.generate_script(video_subject="x")
        self.assertTrue(llm.script_is_complete(script))
        self.assertNotIn("so follow for more quick", script)
        self.assertTrue(script.endswith("Follow for more mind-bending psychology facts."))

    def test_nothing_is_added_when_too_little_text_is_left(self):
        self.assertEqual(llm.repair_truncated_script("Short. Because so"), "Short. Because so")


if __name__ == "__main__":
    unittest.main()
