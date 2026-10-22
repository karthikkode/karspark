# TTS / audio stack

None of these runs as a service. They're **batch tools** that the Kathawin audiobook/video pipeline
calls from `~/kathawin-work` (scratch root, synced to and from the laptop repo
`kathawin_tools/sound/` by `sync.sh`). Each has its own venv because their torch pins clash.

| engine | role (current) | code / venv | weights |
|---|---|---|---|
| **IndexTTS-2** | **narration/dialogue renderer**: holds the narrator's timbre while emotion varies; replaced Fish on 2026-07-30 | `~/index-tts` (upstream `index-tts/index-tts` @ `1349584`, 4 aarch64 patches), `.venv` torch 2.10 | `~/models/indextts-2` (11 GB) |
| **VoxCPM2** | **voice design**: creates the character voices ("VoxCPM2 by ear") | `~/voxcpm-venv` (torch 2.13) | `~/models/voxcpm2` (4.7 GB) |
| Qwen3-TTS VoiceDesign | voice-design candidate | `~/qwentts-venv` | `~/models/qwen3-tts-voice-design` (4.3 GB) |
| Maya1 | expressive TTS candidate (+ SNAC codec) | `~/maya-venv` | `~/models/maya1` (6.2 GB), `~/models/snac-24khz` |
| MOSS VoiceGenerator | rejected for voice design | `~/moss-vg` | `~/models/moss-voicegenerator-runtime` (4.0 GB), `moss-audio-tokenizer` (6.7 GB) |
| Fish Speech S2 | **superseded** by IndexTTS-2 (cloning carried prosody and timbre together, so delivery came out flat) | `~/fish-speech` (2.8 GB) | |
| Whisper | STT for alignment/subtitles | — | `~/models/faster-whisper-medium`, `openai-whisper-medium` |

## How a chapter is rendered

Entry point: `~/kathawin-work/bin/render_chapter.sh [chapter] [story-slug]`. Strictly staged, because
the models can't share 121 GiB:

1. **LLM stage**: `sequence_build.py` (run with the IndexTTS venv's python) calls the local LLM via
   `bin/llm.py` to build the chapter's *director copy* (`production/<slug>/<ch>-director.json`).
   It expects an LLM on :8000.
2. **Make room**: `gpu_residency.sh` stops the LLM unit and polls `/proc/meminfo` until the memory is
   really free. A fixed sleep was racy; see [memory-and-switching.md](../memory-and-switching.md).
3. **Render** with IndexTTS-2, using voices from `voicelib`/`banks`, then score the takes
   (`audio_metrics.py`, `gender.py` machine-checks speaker gender).

`indextts-2.service` appears in other units' `Conflicts=` lists, but **no such unit file exists**. It's
a stale reference ([findings.md](../findings.md) #7).

The pipeline is documented properly in the Kathawin repos (`kathawin_tools/sound/`). This page only covers
what lives on the Spark.
