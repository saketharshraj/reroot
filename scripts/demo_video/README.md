# Demo video tooling

1. `record.py OUT/rec2` drives the live Studio in Chrome (Playwright, `channel="chrome"`) and saves a
   CDP screencast plus an event log (marks, element boxes, cursor moves and clicks).
2. `script2.py` is the narration and edit. Cues in `{braces}` attach to the next spoken word:
   `{src:mark+s}` pins the recording to that word, `{cam:roi}` moves the camera, `{hl:id}` highlights,
   `{g:name}` fires an intro animation.
3. `render2.py OUT` voices each scene with Gemini-TTS (Laomedeia, en-IN), gets word timings from
   Speech-to-Text (Chirp 2), time-warps the footage so events land on their words, and renders
   1080p30 with motion graphics, karaoke captions and sound effects.

`OUT` also needs `assets/` (Badkhal and Aravalli Sentinel-2 stills, the Hindi voice note).
Run with `uv run --with numpy python render2.py OUT` (`--preview` for 540p10).
