import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from automation import video_tail_check as vt  # noqa: E402


def _find_ffmpeg():
    if os.environ.get("FFMPEG"):
        return os.environ["FFMPEG"]
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


FF = _find_ffmpeg()


@unittest.skipIf(FF is None, "ffmpeg niet beschikbaar")
class TailCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        vt.FFMPEG = FF
        cls.d = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def _ff(self, *args):
        subprocess.run([FF, "-y", "-v", "error", *args], check=True)

    def _voice(self, name, fade):
        out = os.path.join(self.d, name)
        af = "afade=t=out:st=4.5:d=0.5" if fade else "anull"
        self._ff("-f", "lavfi", "-i", "sine=frequency=300:duration=5", "-af", af, "-c:a", "libmp3lame", out)
        return out

    def _video(self, name, seconds, audio_seconds):
        out = os.path.join(self.d, name)
        self._ff("-f", "lavfi", "-i", "color=c=black:s=160x284:r=15:d=%s" % seconds,
                 "-f", "lavfi", "-i", "sine=frequency=300:duration=%s" % audio_seconds,
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", out)
        return out

    def test_complete_video_passes(self):
        ok, problems, m = vt.check_tail(self._voice("v_ok.mp3", True), self._video("good.mp4", 5.4, 5.4))
        self.assertTrue(ok, problems)
        self.assertAlmostEqual(m["T"], 5.0, delta=0.15)

    def test_audio_cut_during_assembly_is_caught(self):
        ok, problems, _ = vt.check_tail(self._voice("v_ok2.mp3", True), self._video("short_audio.mp4", 5.4, 3.9))
        self.assertFalse(ok)
        self.assertTrue(any("korter dan de stem" in p for p in problems))

    def test_short_picture_is_caught(self):
        ok, problems, _ = vt.check_tail(self._voice("v_ok3.mp3", True), self._video("short_picture.mp4", 3.9, 5.4))
        self.assertFalse(ok)
        self.assertTrue(any("beeld" in p for p in problems))

    def test_abrupt_voice_end_is_caught(self):
        ok, problems, m = vt.check_tail(self._voice("v_abrupt.mp3", False), self._video("good2.mp4", 5.4, 5.4))
        self.assertFalse(ok)
        self.assertTrue(any("abrupt" in p for p in problems))
        self.assertGreater(m["E"], vt.ABRUPT_END_DB)

    def test_missing_measurement_never_blocks(self):
        ok, problems, _ = vt.check_tail(os.path.join(self.d, "bestaat_niet.mp3"), self._video("good3.mp4", 5.4, 5.4))
        self.assertTrue(ok)
        self.assertTrue(problems)


if __name__ == "__main__":
    unittest.main()
