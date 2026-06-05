# Ramsey Skipper

Watch a Dave Ramsey **Full Show** but skip straight to the parts where he
actually teaches something — facts, numbers, statistics, recent news,
processes, procedures, and how things work. Caller stories, baby-step
pep-talks, emotional segments, ads, and filler get cut.

Give it a YouTube URL. It downloads the timestamped transcript, asks Claude
(`claude-opus-4-8`) to pick the substantive segments, and opens a local web
player that auto-plays only those segments back to back — jumping to the next
when one ends, with controls to step back/forward or keep watching for more
context.

## What you get

- **`run.py`** — run this each time with a YouTube URL.
- **`player.html`** — the browser player (opens automatically; loads `data.js`).
- **`data.js`** — written by `run.py` on each run (the chosen segments).

## Setup (once)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...        # your Anthropic API key
```

## Run (each time)

```bash
python run.py "https://youtu.be/G8syrOa_6Y0"
```

Your browser opens `player.html` and starts playing the kept segments.

### Player controls

- **Prev / Next** — jump between kept segments.
- **⟲ 15s / 15s ⟳** — nudge for a little more context (forward nudges pause
  auto-skip so you don't get yanked to the next segment).
- **Replay segment**, **Play/Pause**.
- **Auto-skip** toggle — turn it off to keep watching past a segment boundary.
- Keyboard: `←` / `→` prev/next, `space` play/pause.

## Options

```
--cookies-from-browser chrome   # if YouTube says "confirm you're not a bot"
--insecure                      # skip TLS verification (only behind a proxy)
--model claude-opus-4-8         # override the model
--no-open                       # don't auto-open the browser
```

## Notes & troubleshooting

- **"Sign in to confirm you're not a bot"** — YouTube is rate-limiting your IP.
  Pass `--cookies-from-browser chrome` (or `firefox`/`edge`/`safari`) so yt-dlp
  uses your logged-in session. This is common on data-center/VPN IPs and rare on
  a normal home connection.
- **The video must have captions** (auto-generated is fine). Dave Ramsey Full
  Shows do.
- Segment selection quality depends on the transcript and the model's judgment;
  if you want more or fewer segments, edit the `SYSTEM_PROMPT` in `run.py`.
- Nothing is re-uploaded: the video streams from YouTube in the IFrame player;
  only the transcript text is sent to the Anthropic API.
