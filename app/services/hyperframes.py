"""
Losse video/beeld-generatie-service bovenop heygen-com/hyperframes (HTML →
MP4/PNG via headless Chrome + FFmpeg, lokaal, geen API-kosten). Staat los van
de bestaande pipeline (volcengine_seedance/ofox/sonilo/video/bgm) — geen van
die bestanden wordt hier vervangen; ``add_bumpers()`` wordt door
automation/daily_pipeline.py aangeroepen als losse, best-effort naveegstap na
de bestaande videogeneratie, nooit ervoor of in plaats van.

Werking: de composities leven als een los Node/npm-project onder
``hyperframes_project/`` (gescaffold met ``npx hyperframes init``). Deze
module roept alleen ``npx hyperframes render --variables '{...}'`` aan als
subprocess. Render draait altijd lokaal (Puppeteer/headless Chrome + ffmpeg),
dus geen account, geen API-key en geen per-render kosten — enige vereiste is
Node 22+ en ffmpeg op de machine.

Drie soorten output:
- ``render_fact_card`` — losse psychology-fact short (9:16, met audio-vrije
  GSAP-animatie op tekst).
- ``render_bumper`` / ``add_bumpers`` — merk-intro/outro (9:16, stil) die om
  een bestaande, al door de pipeline gegenereerde video heen wordt geplakt.
- ``render_carousel`` — set losse PNG-stills (4:5, IG-feed-carousel-ratio)
  voor Instagram-carousel-posts; geen video, geen audio.
"""

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from loguru import logger

# Pin de CLI-versie zodat een render vandaag hetzelfde resultaat geeft als
# over drie maanden — hyperframes wordt via npx bij elke aanroep opgehaald,
# een ongepinde "latest" zou de compositie-output onder ons kunnen laten
# schuiven zonder dat de code hier verandert.
HYPERFRAMES_CLI_VERSION = "0.8.76"
PROJECT_DIR = Path(__file__).resolve().parent.parent.parent / "hyperframes_project"
COMPOSITIONS_DIR = PROJECT_DIR / "compositions"
DEFAULT_RENDER_TIMEOUT_SECONDS = 300.0

# Moet exact matchen met VideoAspect.portrait.to_resolution() in
# app/models/schema.py, anders passen intro/outro/main-video niet op elkaar
# zonder herschalen.
MAIN_VIDEO_WIDTH = 1080
MAIN_VIDEO_HEIGHT = 1920
MAIN_VIDEO_FPS = 30


class HyperframesError(RuntimeError):
    """Deterministische configuratie- of renderfout (geen betaalde upstream-call)."""


@dataclass
class HyperframesRenderResult:
    output_path: Path
    duration_seconds: float
    render_seconds: float


def is_available() -> bool:
    """Node/npx aanwezig en het project-scaffold bestaat."""
    return shutil.which("npx") is not None and (PROJECT_DIR / "index.html").exists()


def _run_render(
    command: list[str],
    project_dir: Path,
    output_path: Path,
    timeout_seconds: float,
) -> float:
    start = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=_render_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise HyperframesError(f"hyperframes render timeout na {timeout_seconds}s") from exc
    elapsed = time.monotonic() - start

    if completed.returncode != 0 or not output_path.exists():
        tail = "\n".join((completed.stdout or "").splitlines()[-30:])
        err_tail = "\n".join((completed.stderr or "").splitlines()[-30:])
        raise HyperframesError(
            f"hyperframes render mislukt (exit={completed.returncode}).\n"
            f"stdout tail:\n{tail}\nstderr tail:\n{err_tail}"
        )
    return elapsed


def _render_composition(
    composition_rel_path: str,
    variables: Mapping[str, Any],
    output_path: Path,
    project_dir: Path,
    timeout_seconds: float,
) -> float:
    """Rendert één opgegeven compositie-HTML met variabelen naar ``output_path``."""
    if shutil.which("npx") is None:
        raise HyperframesError("npx niet gevonden op PATH — Node.js 22+ vereist.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "npx",
        "--yes",
        f"hyperframes@{HYPERFRAMES_CLI_VERSION}",
        "render",
        "-c",
        composition_rel_path,
        "--variables",
        json.dumps(dict(variables), ensure_ascii=False),
        "--output",
        str(output_path.relative_to(project_dir)),
    ]
    return _run_render(command, project_dir, output_path, timeout_seconds)


def render_fact_card(
    hook: str,
    fact: str,
    source: str = "",
    duration: float = 8.0,
    output_name: str | None = None,
    project_dir: Path | None = None,
    timeout_seconds: float = DEFAULT_RENDER_TIMEOUT_SECONDS,
) -> HyperframesRenderResult:
    """
    Rendert één psychology-fact short (1080x1920) via de root-compositie
    ``hyperframes_project/index.html``. Los te gebruiken.
    """
    project_dir = project_dir or PROJECT_DIR
    if not (project_dir / "index.html").exists():
        raise HyperframesError(
            f"Geen hyperframes-compositie gevonden in {project_dir}. "
            "Run eerst `npx hyperframes init` in die map."
        )
    variables: dict[str, Any] = {"hook": hook, "fact": fact, "duration": float(duration)}
    if source:
        variables["source"] = source

    out_dir = project_dir / "out"
    output_name = output_name or f"fact_{int(time.time() * 1000)}.mp4"
    output_path = out_dir / output_name

    logger.info(f"hyperframes render start: hook={hook!r} duration={duration}s")
    elapsed = _render_composition("index.html", variables, output_path, project_dir, timeout_seconds)
    logger.info(f"hyperframes render klaar: {output_path} ({elapsed:.1f}s)")
    return HyperframesRenderResult(
        output_path=output_path, duration_seconds=float(duration), render_seconds=elapsed
    )


def render_fact_cards_batch(
    rows: list[Mapping[str, Any]],
    output_pattern: str = "renders/{name}.mp4",
    project_dir: Path | None = None,
    timeout_seconds: float = DEFAULT_RENDER_TIMEOUT_SECONDS,
) -> Path:
    """
    Batch-render meerdere facts in één hyperframes-run (elk item in ``rows``
    heeft minstens ``name``, ``hook``, ``fact``; optioneel ``source``,
    ``duration``). Handig voor een weekly long-form compilatie of een serie
    shorts in één keer. Geeft de output-map terug.
    """
    project_dir = project_dir or PROJECT_DIR
    if not rows:
        raise HyperframesError("render_fact_cards_batch: rows is leeg")
    if shutil.which("npx") is None:
        raise HyperframesError("npx niet gevonden op PATH — Node.js 22+ vereist.")

    rows_path = project_dir / "out" / f"batch_{int(time.time() * 1000)}.json"
    rows_path.parent.mkdir(parents=True, exist_ok=True)
    rows_path.write_text(json.dumps({"rows": list(rows)}, ensure_ascii=False), encoding="utf-8")

    command = [
        "npx",
        "--yes",
        f"hyperframes@{HYPERFRAMES_CLI_VERSION}",
        "render",
        "--batch",
        str(rows_path.relative_to(project_dir)),
        "--output",
        output_pattern,
    ]
    completed = subprocess.run(
        command,
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=_render_env(),
    )
    if completed.returncode != 0:
        tail = "\n".join((completed.stdout or "").splitlines()[-30:])
        err_tail = "\n".join((completed.stderr or "").splitlines()[-30:])
        raise HyperframesError(
            f"hyperframes batch render mislukt (exit={completed.returncode}).\n"
            f"stdout tail:\n{tail}\nstderr tail:\n{err_tail}"
        )
    return project_dir / "renders"


# ── Merk-bumpers (intro/outro om een bestaande video heen) ─────────────────

def render_bumper(
    kind: str,
    brand: str = "Nognize",
    handle: str = "@nognize",
    cta: str | None = None,
    duration: float | None = None,
    project_dir: Path | None = None,
    timeout_seconds: float = DEFAULT_RENDER_TIMEOUT_SECONDS,
) -> HyperframesRenderResult:
    """kind: "intro" of "outro". Rendert de bijbehorende bumper-compositie."""
    if kind not in ("intro", "outro"):
        raise HyperframesError(f"onbekend bumper-kind: {kind!r} (verwacht intro/outro)")
    project_dir = project_dir or PROJECT_DIR
    composition_rel = f"compositions/bumper_{kind}.html"
    if not (project_dir / composition_rel).exists():
        raise HyperframesError(f"bumper-compositie ontbreekt: {composition_rel}")

    variables: dict[str, Any] = {"brand": brand}
    if kind == "outro":
        variables["handle"] = handle
        if cta:
            variables["cta"] = cta
        variables["duration"] = float(duration) if duration else 1.8
    else:
        variables["duration"] = float(duration) if duration else 1.2

    out_dir = project_dir / "out"
    output_path = out_dir / f"bumper_{kind}_{int(time.time() * 1000)}.mp4"
    elapsed = _render_composition(
        composition_rel, variables, output_path, project_dir, timeout_seconds
    )
    return HyperframesRenderResult(
        output_path=output_path,
        duration_seconds=float(variables["duration"]),
        render_seconds=elapsed,
    )


def add_bumpers(
    main_video_path: str | Path,
    output_path: str | Path | None = None,
    brand: str = "Nognize",
    handle: str = "@nognize",
    cta: str | None = None,
    project_dir: Path | None = None,
    timeout_seconds: float = DEFAULT_RENDER_TIMEOUT_SECONDS,
) -> Path:
    """
    Plakt alleen een outro-bumper (merk-logo + follow-cta) ná
    ``main_video_path`` -- geen intro, de video begint gewoon zoals hij al
    begon. Re-encodeert beide segmenten naar een gemeenschappelijk formaat
    (1080x1920, 30fps, aac 44100 stereo) voor de concat, zodat een
    audio/codec-mismatch tussen de stille outro en de narrated hoofdvideo de
    concat niet laat falen. Bij falen: raise HyperframesError, roeper
    (daily_pipeline.py) vangt dit af en gebruikt gewoon de originele video.
    """
    main_video_path = Path(main_video_path)
    if not main_video_path.exists():
        raise HyperframesError(f"main_video_path bestaat niet: {main_video_path}")
    if shutil.which("ffmpeg") is None:
        raise HyperframesError("ffmpeg niet gevonden op PATH.")

    outro = render_bumper(
        "outro", brand=brand, handle=handle, cta=cta, project_dir=project_dir, timeout_seconds=timeout_seconds
    )

    output_path = Path(output_path) if output_path else main_video_path.with_name(
        f"{main_video_path.stem}_bumpered{main_video_path.suffix}"
    )

    # filter_complex concat i.p.v. de concat-demuxer: transcodeert elk
    # segment naar identieke video/audio-parameters, dus werkt ook als de
    # hoofdvideo een andere audio sample rate/codec heeft dan de (stille)
    # outro. anullsrc levert de stille audiotrack voor de outro.
    w, h, fps = MAIN_VIDEO_WIDTH, MAIN_VIDEO_HEIGHT, MAIN_VIDEO_FPS
    command = [
        "ffmpeg",
        "-y",
        "-i", str(main_video_path),              # 0: main video (has audio)
        "-i", str(outro.output_path),            # 1: outro video (silent, no audio stream)
        "-f", "lavfi", "-t", str(outro.duration_seconds),
        "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",  # 2: silence for outro
        "-filter_complex",
        (
            f"[0:v]scale={w}:{h},setsar=1,fps={fps}[v0];"
            f"[1:v]scale={w}:{h},setsar=1,fps={fps}[v1];"
            "[0:a]aformat=sample_rates=44100:channel_layouts=stereo[a0];"
            "[v0][a0][v1][2:a]concat=n=2:v=1:a=1[outv][outa]"
        ),
        "-map", "[outv]",
        "-map", "[outa]",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-pix_fmt", "yuv420p",
        str(output_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds)
    if completed.returncode != 0 or not output_path.exists():
        err_tail = "\n".join((completed.stderr or "").splitlines()[-30:])
        raise HyperframesError(f"ffmpeg concat mislukt (exit={completed.returncode}):\n{err_tail}")
    return output_path


# ── Carousel-stills voor Instagram-feed ─────────────────────────────────────

@dataclass
class CarouselSlideSpec:
    kind: str  # "hook" | "fact" | "cta"
    text: str


def render_carousel(
    slides: list[CarouselSlideSpec],
    brand: str = "Nognize",
    out_dir: Path | None = None,
    project_dir: Path | None = None,
    timeout_seconds: float = DEFAULT_RENDER_TIMEOUT_SECONDS,
) -> list[Path]:
    """
    Rendert elke slide als losse PNG-still (1080x1350, IG-feed 4:5-ratio) via
    ``compositions/carousel_slide.html``. Elke slide wordt intern als een
    kort videoclipje gerenderd en het laatste frame (na de reveal-animatie)
    wordt met ffmpeg als PNG uitgesneden — zelfde truc als de handmatige
    preview-frames die tijdens het testen van deze service zijn gebruikt.
    Geeft de lijst PNG-paden terug, in slide-volgorde.
    """
    if not slides:
        raise HyperframesError("render_carousel: slides is leeg")
    project_dir = project_dir or PROJECT_DIR
    composition_rel = "compositions/carousel_slide.html"
    if not (project_dir / composition_rel).exists():
        raise HyperframesError(f"carousel-compositie ontbreekt: {composition_rel}")
    if shutil.which("ffmpeg") is None:
        raise HyperframesError("ffmpeg niet gevonden op PATH.")

    batch_id = int(time.time() * 1000)
    out_dir = out_dir or (project_dir / "out" / f"carousel_{batch_id}")
    out_dir.mkdir(parents=True, exist_ok=True)

    still_paths: list[Path] = []
    total = len(slides)
    for i, slide in enumerate(slides, start=1):
        variables = {
            "kind": slide.kind,
            "text": slide.text,
            "index": i,
            "total": total,
            "brand": brand,
        }
        clip_path = out_dir / f"_slide_{i}_clip.mp4"
        _render_composition(composition_rel, variables, clip_path, project_dir, timeout_seconds)

        still_path = out_dir / f"slide_{i}.png"
        # Grijp het frame ná de reveal-animatie (compositie-duur is 1.2s,
        # animatie is klaar bij ~1.1s) zodat de tekst volledig zichtbaar is,
        # niet halverwege een fade-in.
        extract_cmd = [
            "ffmpeg", "-y",
            "-i", str(clip_path),
            "-ss", "1.1",
            "-frames:v", "1",
            str(still_path),
        ]
        completed = subprocess.run(extract_cmd, capture_output=True, text=True, timeout=60)
        clip_path.unlink(missing_ok=True)
        if completed.returncode != 0 or not still_path.exists():
            err_tail = "\n".join((completed.stderr or "").splitlines()[-20:])
            raise HyperframesError(f"frame-extractie mislukt voor slide {i}:\n{err_tail}")
        still_paths.append(still_path)

    logger.info(f"hyperframes carousel klaar: {len(still_paths)} slides in {out_dir}")
    return still_paths


def _render_env() -> dict[str, str]:
    import os

    env = os.environ.copy()
    # skills-check belt telemetry/GitHub-lookup bij elke render af zonder
    # netwerkeffect op het resultaat — puur CI/batch-gebruik.
    env["HYPERFRAMES_SKIP_SKILLS"] = "1"
    return env
