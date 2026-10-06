from app.services import subtitle


def test_correct_keeps_transcript_when_script_normalizes_to_nothing(tmp_path):
    srt = tmp_path / "s.srt"
    body = "1\n00:00:00,000 --> 00:00:01,000\nhello world\n\n"
    srt.write_text(body, encoding="utf-8")
    subtitle.correct(str(srt), "---")
    assert srt.read_text(encoding="utf-8") == body
