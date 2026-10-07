from unittest.mock import patch

from app.services import llm


def test_social_metadata_error_text_inside_caption_is_kept():
    ok = '{"title": "T", "caption": "Fix Error: disk full", "hashtags": ["#a", "#b", "#c"]}'
    with patch.object(llm, "_generate_response", return_value=ok) as resp:
        out = llm.generate_social_metadata(video_subject="Storage", platform="youtube_shorts")
    assert resp.call_count == 1
    assert "Error: disk full" in out["caption"]


def test_social_metadata_error_prefix_still_falls_back():
    with patch.object(llm, "_generate_response", return_value="Error: provider unavailable") as resp:
        out = llm.generate_social_metadata(video_subject="Storage", platform="youtube_shorts")
    assert resp.call_count == 1
    assert out["title"]
