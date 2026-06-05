#!/usr/bin/env python3
"""
Ramsey Skipper
==============

Take a Dave Ramsey "Full Show" YouTube URL, download its timestamped transcript
with yt-dlp, ask Claude (claude-opus-4-8) to keep only the segments where Dave
actually *teaches* something — facts, numbers, statistics, recent news,
processes, procedures, how things work — and drop the caller stories, baby-step
basics, emotional back-and-forth, ads, and filler.

The result is written to ``data.js`` next to ``player.html``. The player uses the
YouTube IFrame API to auto-play only the kept segments back to back, jumping to
the next one when each ends, while still letting you step back/forward or keep
watching past a boundary for more context.

Usage
-----
    export ANTHROPIC_API_KEY=sk-ant-...
    python run.py "https://youtu.be/G8syrOa_6Y0"

Useful flags
------------
    --cookies-from-browser chrome   # if YouTube asks you to "confirm you're not a bot"
    --insecure                      # skip TLS verification (only behind a proxy)
    --model claude-opus-4-8         # override the model
    --no-open                       # don't auto-open the browser
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import webbrowser
from html import unescape
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_MODEL = "claude-opus-4-8"


# --------------------------------------------------------------------------- #
# YouTube helpers
# --------------------------------------------------------------------------- #
def extract_video_id(url: str) -> str:
    """Pull the 11-char video id out of any common YouTube URL form."""
    url = url.strip()
    patterns = [
        r"(?:v=|/v/|youtu\.be/|/embed/|/shorts/)([0-9A-Za-z_-]{11})",
        r"^([0-9A-Za-z_-]{11})$",  # a bare id
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    raise ValueError(f"Could not find a YouTube video id in: {url!r}")


def download_subtitles(url: str, tmpdir: Path, *, insecure: bool,
                       cookies_from_browser: str | None) -> Path:
    """Download English subtitles (manual if present, else auto) as a .vtt file."""
    out_tmpl = str(tmpdir / "%(id)s.%(ext)s")
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", "en.*",
        "--sub-format", "vtt",
        "-o", out_tmpl,
    ]
    if insecure:
        cmd.append("--no-check-certificates")
    if cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    cmd.append(url)

    print("· Downloading transcript with yt-dlp …")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit(
            "\nyt-dlp failed. Common fixes:\n"
            "  • YouTube says 'confirm you're not a bot'  →  add "
            "--cookies-from-browser chrome (or firefox/edge/safari)\n"
            "  • TLS / certificate errors behind a proxy   →  add --insecure\n"
        )

    vtts = sorted(tmpdir.glob("*.vtt"))
    if not vtts:
        raise SystemExit(
            "No subtitles were found for this video. It may have captions "
            "disabled. Try a different Full Show upload."
        )
    # Prefer a manual track over an automatic one if both exist.
    manual = [p for p in vtts if ".en." in p.name and "auto" not in p.name.lower()]
    return (manual or vtts)[0]


# --------------------------------------------------------------------------- #
# VTT parsing
# --------------------------------------------------------------------------- #
_TS = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})\.(\d{3})"
)
_TAG = re.compile(r"<[^>]+>")  # <00:00:01.000>, <c>, </c>, etc.


def _to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_vtt(path: Path) -> list[tuple[float, float, str]]:
    """Return de-duplicated (start, end, text) cues from a WebVTT file."""
    raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
    cues: list[tuple[float, float, str]] = []
    i = 0
    while i < len(raw):
        m = _TS.search(raw[i])
        if not m:
            i += 1
            continue
        start = _to_seconds(*m.group(1, 2, 3, 4))
        end = _to_seconds(*m.group(5, 6, 7, 8))
        i += 1
        lines: list[str] = []
        while i < len(raw) and raw[i].strip() and not _TS.search(raw[i]):
            lines.append(raw[i])
            i += 1
        text = " ".join(lines)
        text = _TAG.sub("", text)            # strip inline word-timing/markup
        text = unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            cues.append((start, end, text))

    # YouTube auto-captions repeat each line as it scrolls. Drop a cue whose
    # text is contained in the text we already kept just before it.
    deduped: list[tuple[float, float, str]] = []
    prev_text = ""
    for start, end, text in cues:
        if text == prev_text or (prev_text and text in prev_text):
            if deduped:  # extend the previous cue's end time
                ps, _, pt = deduped[-1]
                deduped[-1] = (ps, end, pt)
            continue
        # If the new text simply appends to prev (rolling caption), keep only the tail.
        if prev_text and text.startswith(prev_text):
            tail = text[len(prev_text):].strip()
            if tail:
                deduped.append((start, end, tail))
            prev_text = text
            continue
        deduped.append((start, end, text))
        prev_text = text
    return deduped


def build_transcript(cues: list[tuple[float, float, str]],
                     bucket_seconds: float = 12.0) -> str:
    """Group cues into ~bucket_seconds lines tagged with their start second."""
    lines: list[str] = []
    buf: list[str] = []
    bucket_start: float | None = None
    for start, _end, text in cues:
        if bucket_start is None:
            bucket_start = start
        buf.append(text)
        if start - bucket_start >= bucket_seconds or len(" ".join(buf)) > 240:
            lines.append(f"[{int(bucket_start)}] {' '.join(buf)}")
            buf, bucket_start = [], None
    if buf and bucket_start is not None:
        lines.append(f"[{int(bucket_start)}] {' '.join(buf)}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Claude
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """\
You are an editor for a financial-education channel. You receive the full
timestamped transcript of a Dave Ramsey "Full Show" episode and select ONLY the
spans worth keeping for a viewer who wants substance, not entertainment.

KEEP spans where Dave (or a co-host/guest) actually teaches or informs:
  • concrete facts, numbers, statistics, percentages, dollar figures
  • recent news, current events, market/economic commentary
  • processes, procedures, step-by-step "how to" explanations
  • how something works (how a mortgage, index fund, HSA, tax, etc. functions)
  • specific, generalizable financial advice with reasoning behind it

SKIP everything else:
  • individual caller stories, personal anecdotes, chit-chat, banter
  • the generic "baby steps" pep-talk basics with no new information
  • emotional / motivational / venting conversations
  • advertisements, sponsor reads, plugs, call-in instructions, intros/outros
  • filler, repetition, dead air

Rules for the output:
  • Each segment must map to a real, contiguous stretch of the transcript.
  • Timestamps in the transcript are in [seconds]. Return start and end in
    whole seconds. end must be greater than start.
  • Prefer meaningful chunks (roughly 20 seconds to 5 minutes). Merge adjacent
    teaching moments rather than emitting many tiny fragments.
  • Order segments by start time and do not overlap them.
  • "title" = a short, specific label (e.g. "How an HSA triple tax advantage works").
  • "reason" = one short phrase naming why it's kept (e.g. "explains 2024 IRA limits").
  • If almost nothing qualifies, return only the few spans that genuinely do.
"""

SEGMENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "title": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["start", "end", "title", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["segments"],
    "additionalProperties": False,
}


def extract_segments(transcript: str, duration: float | None, model: str) -> list[dict]:
    import anthropic

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise SystemExit(
            "ANTHROPIC_API_KEY is not set. Export your key first:\n"
            "    export ANTHROPIC_API_KEY=sk-ant-...\n"
        )

    client = anthropic.Anthropic()
    dur_note = f"\n\nThe episode is about {int(duration)} seconds long." if duration else ""
    user_msg = (
        "Here is the timestamped transcript. Return the segments to keep."
        + dur_note
        + "\n\n<transcript>\n"
        + transcript
        + "\n</transcript>"
    )

    print(f"· Asking {model} to find the substantive segments …")
    # Stream so a long response can't hit an HTTP idle timeout; structured
    # outputs guarantee the first text block is valid JSON matching the schema.
    with client.messages.stream(
        model=model,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        output_config={
            "format": {"type": "json_schema", "schema": SEGMENTS_SCHEMA}
        },
        messages=[{"role": "user", "content": user_msg}],
    ) as stream:
        message = stream.get_final_message()

    text = next((b.text for b in message.content if b.type == "text"), None)
    if not text:
        raise SystemExit("Claude returned no JSON output.")
    data = json.loads(text)
    return data.get("segments", [])


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def clean_segments(segments: list[dict], duration: float | None) -> list[dict]:
    """Sort, clamp, and drop degenerate/overlapping segments."""
    cleaned: list[dict] = []
    for s in segments:
        try:
            start = max(0.0, float(s["start"]))
            end = float(s["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if duration:
            end = min(end, duration)
        if end - start < 3:  # skip sub-3-second fragments
            continue
        cleaned.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "title": str(s.get("title", "")).strip() or "Segment",
            "reason": str(s.get("reason", "")).strip(),
        })
    cleaned.sort(key=lambda s: s["start"])
    # Remove overlaps by nudging starts forward.
    out: list[dict] = []
    for s in cleaned:
        if out and s["start"] < out[-1]["end"]:
            s["start"] = out[-1]["end"]
            if s["end"] - s["start"] < 3:
                continue
        out.append(s)
    return out


def write_data_js(video_id: str, segments: list[dict], source_url: str,
                  title: str | None) -> Path:
    payload = {
        "videoId": video_id,
        "sourceUrl": source_url,
        "title": title or "",
        "segments": segments,
    }
    data_path = HERE / "data.js"
    data_path.write_text(
        "// Generated by run.py — do not edit by hand.\n"
        "window.RAMSEY = " + json.dumps(payload, indent=2) + ";\n",
        encoding="utf-8",
    )
    return data_path


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="Filter a Dave Ramsey Full Show to its substantive parts.")
    ap.add_argument("url", help="YouTube URL (or bare 11-char video id)")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"Claude model (default: {DEFAULT_MODEL})")
    ap.add_argument("--cookies-from-browser", default=None,
                    help="Pass browser cookies to yt-dlp, e.g. chrome / firefox / edge")
    ap.add_argument("--insecure", action="store_true", help="Skip TLS cert verification in yt-dlp")
    ap.add_argument("--no-open", action="store_true", help="Do not open the browser automatically")
    args = ap.parse_args()

    video_id = extract_video_id(args.url)
    print(f"· Video id: {video_id}")

    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        vtt = download_subtitles(
            args.url, tmpdir,
            insecure=args.insecure,
            cookies_from_browser=args.cookies_from_browser,
        )
        cues = parse_vtt(vtt)

    if not cues:
        raise SystemExit("Transcript was empty after parsing.")
    duration = cues[-1][1]
    print(f"· Parsed {len(cues)} caption cues (~{int(duration//60)} min).")

    transcript = build_transcript(cues)
    segments = extract_segments(transcript, duration, args.model)
    segments = clean_segments(segments, duration)
    if not segments:
        raise SystemExit("Claude did not flag any substantive segments for this video.")

    kept = sum(s["end"] - s["start"] for s in segments)
    print(f"· Kept {len(segments)} segments — {int(kept//60)}m{int(kept%60):02d}s "
          f"of ~{int(duration//60)}m total.")

    data_path = write_data_js(video_id, segments, args.url, None)
    player = HERE / "player.html"
    if not player.exists():
        raise SystemExit(
            f"player.html is missing next to run.py (expected at {player})."
        )
    print(f"· Wrote {data_path.name}")

    if not args.no_open:
        url = player.as_uri()
        print(f"· Opening {url}")
        webbrowser.open(url)
    else:
        print(f"· Open {player} in your browser to watch.")


if __name__ == "__main__":
    main()
