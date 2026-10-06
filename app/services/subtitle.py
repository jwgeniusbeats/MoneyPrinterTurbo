import difflib
import json
import os.path
import re
from timeit import default_timer as timer

try:
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None
from loguru import logger

from app.config import config
from app.utils import utils

model_size = config.whisper.get("model_size", "large-v3")
device = config.whisper.get("device", "cpu")
compute_type = config.whisper.get("compute_type", "int8")
initial_prompt = config.whisper.get("initial_prompt", "") or None
model = None


def _ensure_model() -> bool:
    """Assumes WhisperModel is already known to be importable -- callers
    check that separately, since "dependency not installed" and "model
    failed to load" are distinct, both-falsy-but-different signals create()
    returns ("" vs None respectively)."""
    global model
    if model:
        return True

    model_path = f"{utils.root_dir()}/models/whisper-{model_size}"
    model_bin_file = f"{model_path}/model.bin"
    if not os.path.isdir(model_path) or not os.path.isfile(model_bin_file):
        model_path = model_size

    logger.info(
        f"loading model: {model_path}, device: {device}, compute_type: {compute_type}"
    )
    try:
        model = WhisperModel(
            model_size_or_path=model_path, device=device, compute_type=compute_type
        )
        return True
    except Exception as e:
        logger.error(
            f"failed to load model: {e} \n\n"
            f"********************************************\n"
            f"this may be caused by network issue. \n"
            f"please download the model manually and put it in the 'models' folder. \n"
            f"see [README.md FAQ](https://github.com/harry0703/MoneyPrinterTurbo) for more details.\n"
            f"********************************************\n\n"
        )
        return False


def create(audio_file, subtitle_file: str = "", word_level: bool = False):
    """Transcribes audio_file into subtitle_file (SRT) and ALSO returns the
    flat per-word timestamps faster-whisper already computes internally
    (word_timestamps=True) -- reused by correct() below so a
    subtitle_provider="whisper" + sentence-display run only transcribes the
    audio once instead of twice."""
    if WhisperModel is None:
        logger.warning("faster_whisper not available, skipping whisper subtitle generation")
        return ""
    if not _ensure_model():
        return None

    logger.info(f"start, output file: {subtitle_file}")
    if not subtitle_file:
        subtitle_file = f"{audio_file}.srt"

    segments, info = model.transcribe(
        audio_file,
        beam_size=5,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
        **({"initial_prompt": initial_prompt} if initial_prompt else {}),
    )

    logger.info(
        f"detected language: '{info.language}', probability: {info.language_probability:.2f}"
    )

    start = timer()
    subtitles = []
    words_out = []

    def recognized(seg_text, seg_start, seg_end):
        seg_text = seg_text.strip()
        if not seg_text:
            return

        msg = "[%.2fs -> %.2fs] %s" % (seg_start, seg_end, seg_text)
        logger.debug(msg)

        subtitles.append(
            {"msg": seg_text, "start_time": seg_start, "end_time": seg_end}
        )

    for segment in segments:
        for word in segment.words or []:
            cleaned = word.word.strip()
            if cleaned:
                words_out.append({"word": cleaned, "start": word.start, "end": word.end})

        if word_level and segment.words:
            for word in segment.words:
                cleaned_word = word.word.strip()
                if cleaned_word:
                    recognized(cleaned_word, word.start, word.end)
            continue

        words_idx = 0
        words_len = len(segment.words)

        seg_start = 0
        seg_end = 0
        seg_text = ""

        if segment.words:
            is_segmented = False
            for word in segment.words:
                if not is_segmented:
                    seg_start = word.start
                    is_segmented = True

                seg_end = word.end
                # If it contains punctuation, then break the sentence.
                seg_text += word.word

                if utils.str_contains_punctuation(word.word):
                    # remove last char
                    seg_text = seg_text[:-1]
                    if not seg_text:
                        continue

                    recognized(seg_text, seg_start, seg_end)

                    is_segmented = False
                    seg_text = ""

                if words_idx == 0 and segment.start < word.start:
                    seg_start = word.start
                if words_idx == (words_len - 1) and segment.end > word.end:
                    seg_end = word.end
                words_idx += 1

        if not seg_text:
            continue

        recognized(seg_text, seg_start, seg_end)

    end = timer()

    diff = end - start
    logger.info(f"complete, elapsed: {diff:.2f} s")

    idx = 1
    lines = []
    for subtitle in subtitles:
        text = subtitle.get("msg")
        if text:
            lines.append(
                utils.text_to_srt(
                    idx, text, subtitle.get("start_time"), subtitle.get("end_time")
                )
            )
            idx += 1

    sub = "\n".join(lines) + "\n"
    with open(subtitle_file, "w", encoding="utf-8") as f:
        f.write(sub)
    logger.info(f"subtitle file created: {subtitle_file}")
    return words_out


def file_to_subtitles(filename):
    if not filename or not os.path.isfile(filename):
        return []

    times_texts = []
    current_times = None
    current_text = ""
    index = 0
    with open(filename, "r", encoding="utf-8") as f:
        for line in f:
            times = re.findall("([0-9]*:[0-9]*:[0-9]*,[0-9]*)", line)
            # A timestamp-looking string can appear inside a cue's own text
            # (e.g. spoken narration referencing a time). Only treat it as
            # the cue's timing line when we're not already inside one --
            # otherwise it would silently overwrite the real timing.
            if times and current_times is None:
                current_times = line
            elif line.strip() == "" and current_times:
                index += 1
                times_texts.append((index, current_times.strip(), current_text.strip()))
                current_times, current_text = None, ""
            elif current_times:
                current_text += line

    # Flush the final block. SRT files whose last subtitle is not followed by a
    # trailing blank line never hit the blank-line branch above, so without this
    # the last subtitle would be silently dropped.
    if current_times:
        index += 1
        times_texts.append((index, current_times.strip(), current_text.strip()))
    return times_texts


def levenshtein_distance(s1, s2):
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def similarity(a, b):
    distance = levenshtein_distance(a.lower(), b.lower())
    max_length = max(len(a), len(b))
    return 1 - (distance / max_length)


def _normalize_word_for_alignment(word: str) -> str:
    return re.sub(r"[^\w']", "", word).lower()


def _correct_from_words(subtitle_file, video_script, words: list):
    """Rebuilds the subtitle file straight from per-word whisper timestamps
    instead of fuzzy-matching whole lines of text.

    Why: the old text-similarity approach (still available below as the
    fallback when `words` isn't supplied) compares each of OUR OWN script
    clauses -- capped at MAX_SUBTITLE_CLAUSE_WORDS, see
    utils._cap_clause_word_count() -- against whisper's own independently
    punctuation-segmented output. Those two segmentations diverge on any
    clause longer than the cap with no internal punctuation (whisper keeps
    it as one segment; we split it into several), which desyncs every
    subsequent line for the rest of the video. Aligning at WORD granularity
    sidesteps that entirely: our clause boundaries are just an index range
    into the same underlying word sequence whisper transcribed, so this
    only needs to find where each of our words landed in whisper's word
    list, never to reconcile two different sentence-boundary choices.
    """
    normalized_script = utils.normalize_script_for_subtitle_matching(video_script)
    script_lines = utils.split_string_by_punctuations(normalized_script)

    clause_word_lists = [line.split() for line in script_lines if line.strip()]
    script_words = []
    clause_ranges = []
    for word_list in clause_word_lists:
        start = len(script_words)
        script_words.extend(word_list)
        clause_ranges.append((start, len(script_words)))

    norm_script = [_normalize_word_for_alignment(w) for w in script_words]
    norm_whisper = [_normalize_word_for_alignment(w["word"]) for w in words]

    matcher = difflib.SequenceMatcher(None, norm_script, norm_whisper, autojunk=False)
    idx_map = {}
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            # An empty normalized token (whisper words that are pure
            # punctuation, e.g. "--") can spuriously "match" another empty
            # token -- never trust an empty-string match for timing.
            if norm_script[block.a + k]:
                idx_map[block.a + k] = block.b + k

    lines = []
    last_end_time = 0.0
    for line_text, (start_idx, end_idx) in zip(script_lines, clause_ranges):
        if not line_text.strip():
            continue

        matched_positions = [
            idx_map[i] for i in range(start_idx, end_idx) if i in idx_map
        ]
        if matched_positions:
            start_time = words[min(matched_positions)]["start"]
            end_time = words[max(matched_positions)]["end"]
        else:
            # No word in this clause matched anywhere in the whisper
            # transcript (e.g. a heavily mispronounced/garbled clause) --
            # place it right after the previous clause rather than at
            # 00:00:00, so it doesn't visually jump to the start of the
            # video. A short, arbitrary but bounded placeholder duration.
            logger.warning(f"No whisper alignment found for clause: {line_text!r}")
            start_time = last_end_time
            end_time = last_end_time + max(0.5, 0.3 * len(line_text.split()))

        lines.append(utils.text_to_srt(len(lines) + 1, line_text, start_time, end_time))
        last_end_time = end_time

    with open(subtitle_file, "w", encoding="utf-8") as fd:
        fd.write("\n".join(lines) + "\n")
    logger.info(f"Subtitle corrected via word-level alignment ({len(lines)} lines)")


def correct(subtitle_file, video_script, words: list | None = None):
    if words:
        _correct_from_words(subtitle_file, video_script, words)
        return

    subtitle_items = file_to_subtitles(subtitle_file)
    normalized_script = utils.normalize_script_for_subtitle_matching(video_script)
    script_lines = utils.split_string_by_punctuations(normalized_script)

    corrected = False
    new_subtitle_items = []
    script_index = 0
    subtitle_index = 0

    while script_index < len(script_lines) and subtitle_index < len(subtitle_items):
        script_line = script_lines[script_index].strip()
        subtitle_line = subtitle_items[subtitle_index][2].strip()

        if script_line == subtitle_line:
            new_subtitle_items.append(subtitle_items[subtitle_index])
            script_index += 1
            subtitle_index += 1
        else:
            combined_subtitle = subtitle_line
            start_time = subtitle_items[subtitle_index][1].split(" --> ")[0]
            end_time = subtitle_items[subtitle_index][1].split(" --> ")[1]
            next_subtitle_index = subtitle_index + 1

            while next_subtitle_index < len(subtitle_items):
                next_subtitle = subtitle_items[next_subtitle_index][2].strip()
                if similarity(
                    script_line, combined_subtitle + " " + next_subtitle
                ) > similarity(script_line, combined_subtitle):
                    combined_subtitle += " " + next_subtitle
                    end_time = subtitle_items[next_subtitle_index][1].split(" --> ")[1]
                    next_subtitle_index += 1
                else:
                    break

            if similarity(script_line, combined_subtitle) > 0.8:
                logger.warning(
                    f"Merged/Corrected - Script: {script_line}, Subtitle: {combined_subtitle}"
                )
                new_subtitle_items.append(
                    (
                        len(new_subtitle_items) + 1,
                        f"{start_time} --> {end_time}",
                        script_line,
                    )
                )
                corrected = True
            else:
                logger.warning(
                    f"Mismatch - Script: {script_line}, Subtitle: {combined_subtitle}"
                )
                new_subtitle_items.append(
                    (
                        len(new_subtitle_items) + 1,
                        f"{start_time} --> {end_time}",
                        script_line,
                    )
                )
                corrected = True

            script_index += 1
            subtitle_index = next_subtitle_index

    # Process the remaining lines of the script.
    while script_index < len(script_lines):
        logger.warning(f"Extra script line: {script_lines[script_index]}")
        if subtitle_index < len(subtitle_items):
            new_subtitle_items.append(
                (
                    len(new_subtitle_items) + 1,
                    subtitle_items[subtitle_index][1],
                    script_lines[script_index],
                )
            )
            subtitle_index += 1
        else:
            new_subtitle_items.append(
                (
                    len(new_subtitle_items) + 1,
                    "00:00:00,000 --> 00:00:00,000",
                    script_lines[script_index],
                )
            )
        script_index += 1
        corrected = True

    if corrected and not new_subtitle_items:
        # Mirrors upstream e053af0: never replace a transcript with a cue-less file.
        logger.warning("Subtitle correction produced no cues, keeping original")
    elif corrected:
        with open(subtitle_file, "w", encoding="utf-8") as fd:
            for i, item in enumerate(new_subtitle_items):
                fd.write(f"{i + 1}\n{item[1]}\n{item[2]}\n\n")
        logger.info("Subtitle corrected")
    else:
        logger.success("Subtitle is correct")


if __name__ == "__main__":
    task_id = "c12fd1e6-4b0a-4d65-a075-c87abe35a072"
    task_dir = utils.task_dir(task_id)
    subtitle_file = f"{task_dir}/subtitle.srt"
    audio_file = f"{task_dir}/audio.mp3"

    subtitles = file_to_subtitles(subtitle_file)
    print(subtitles)

    script_file = f"{task_dir}/script.json"
    with open(script_file, "r") as f:
        script_content = f.read()
    s = json.loads(script_content)
    script = s.get("script")

    correct(subtitle_file, script)

    subtitle_file = f"{task_dir}/subtitle-test.srt"
    create(audio_file, subtitle_file)
