import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from automation import self_update as su  # noqa: E402


def g(cwd, *a):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t"] + list(a), cwd=cwd, check=True,
                   capture_output=True)


class SelfUpdateTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        r = self.t.name
        self.origin, self.work, self.other = [os.path.join(r, n) for n in ("o.git", "w", "x")]
        subprocess.run(["git", "init", "-q", "--bare", self.origin], check=True)
        subprocess.run(["git", "clone", "-q", self.origin, self.work], check=True, capture_output=True)
        g(self.work, "checkout", "-q", "-b", "br")
        open(os.path.join(self.work, "a.txt"), "w").write("1\n")
        g(self.work, "add", "."); g(self.work, "commit", "-qm", "init"); g(self.work, "push", "-q", "origin", "br")
        subprocess.run(["git", "clone", "-q", "-b", "br", self.origin, self.other], check=True, capture_output=True)
        su.LOG_FILE = os.path.join(r, "log")

    def tearDown(self):
        self.t.cleanup()

    def _push_from_other(self, name, text):
        open(os.path.join(self.other, name), "w").write(text)
        g(self.other, "add", "."); g(self.other, "commit", "-qm", "x"); g(self.other, "push", "-q", "origin", "br")

    def test_current_when_nothing_new(self):
        self.assertEqual(su.update(self.work, "br"), "current")

    def test_updates_and_keeps_dirty_unrelated_and_staged_files(self):
        self._push_from_other("new.txt", "n\n")
        open(os.path.join(self.work, "a.txt"), "w").write("dirty\n")  # onrelateerde lokale wijziging (zoals post_log)
        open(os.path.join(self.work, "staged.txt"), "w").write("s\n")
        g(self.work, "add", "staged.txt")
        self.assertEqual(su.update(self.work, "br"), "updated")
        self.assertTrue(os.path.exists(os.path.join(self.work, "new.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.work, "staged.txt")))
        self.assertEqual(open(os.path.join(self.work, "a.txt")).read(), "dirty\n")

    def test_conflict_aborts_and_changes_nothing(self):
        open(os.path.join(self.work, "a.txt"), "w").write("local\n")
        g(self.work, "commit", "-qam", "local")
        self._push_from_other("a.txt", "remote\n")
        self.assertEqual(su.update(self.work, "br"), "conflict")
        self.assertEqual(open(os.path.join(self.work, "a.txt")).read(), "local\n")
        self.assertFalse(os.path.exists(os.path.join(self.work, ".git", "MERGE_HEAD")))

    def test_fetch_failure_never_raises(self):
        self.assertEqual(su.update(self.work, "bestaat-niet"), "skipped")


if __name__ == "__main__":
    unittest.main()
