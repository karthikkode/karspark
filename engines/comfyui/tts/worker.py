#!/usr/bin/env python3
"""IndexTTS-2.5 worker, started by the adapter (../adapter/server.py) the first time a speech job runs.

Runs in its own venv (/opt/tts/venv: IndexTTS pins transformers 4.52; ComfyUI runs 5.x) on the image's
torch. The adapter stops it on /v1/free, which is how core frees its memory before another model family.

Protocol: one JSON object per line on stdin, one reply per line on the original stdout. Everything the
libraries print goes to stderr (the container log).
  {"cmd": "synth", <spec from workflows.build_speech>}
      -> {"state": "succeeded", "outputs": ["<id>/out/speech.wav"], "duration_s": ...}
       | {"state": "paused"} | {"state": "failed", "error": "..."}

Lossless pause: the text is split into chunks of whole sentences. Each chunk is written to ckpt/ as it
finishes, and the PAUSE file is checked between chunks. Running the job again skips finished chunks;
each chunk is seeded from (seed, index), so the result matches an uninterrupted run.
"""
import json
import os
import re
import sys
import time
import traceback
import wave
from pathlib import Path

PROTO = os.fdopen(os.dup(1), "w", buffering=1)
os.dup2(2, 1)                      # library prints -> stderr
sys.stdout = sys.stderr

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402
import torchaudio  # noqa: E402

SR = 22050
CHUNK_CHARS = 240                  # whole sentences per chunk; IndexTTS splits further inside
SENTENCE_GAP_MS = 250
CKPT_LINKS = Path("/tmp/indextts-ckpt")


def _ta_load(path, *a, **k):
    """torchaudio >= 2.9 loads through TorchCodec, which this image doesn't ship; soundfile does the job."""
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return torch.from_numpy(data.T.copy()), sr


torchaudio.load = _ta_load
_tts = None


def model(paths):
    """Load once. The upstream code wants every file under one model dir (aux models in hf_cache/):
    link our ~/models layout into that shape instead of copying or downloading anything."""
    global _tts
    if _tts is not None:
        return _tts
    d = CKPT_LINKS
    (d / "hf_cache").mkdir(parents=True, exist_ok=True)
    for f in Path(paths["model_dir"]).iterdir():
        if not (d / f.name).exists():
            (d / f.name).symlink_to(f)
    for name, src in (("w2v-bert-2.0", paths["w2v_bert"]), ("campplus_cn_common.bin", paths["campplus"]),
                      ("bigvgan", paths["bigvgan"])):
        if not (d / "hf_cache" / name).exists():
            (d / "hf_cache" / name).symlink_to(src)
    from indextts.utils import front

    def load_wetext(self):
        # The upstream Linux path needs pynini (WeTextProcessing), which has no aarch64 wheel; wetext ships
        # the same grammars as compiled FSTs and runs on kaldifst. This is upstream's own non-Linux branch.
        if self.zh_normalizer is None or self.en_normalizer is None:
            from wetext import Normalizer
            self.zh_normalizer = Normalizer(remove_erhua=False, lang="zh", operator="tn")
            self.en_normalizer = Normalizer(lang="en", operator="tn")
    front.TextNormalizer.load = load_wetext
    from indextts.infer_v2_5 import IndexTTS2
    t0 = time.time()
    _tts = IndexTTS2(cfg_path=str(d / "config.yaml"), model_dir=str(d), use_bf16=True, use_cuda_kernel=False)
    print(f"[tts] IndexTTS-2.5 loaded in {time.time() - t0:.0f} s", flush=True)
    return _tts


def chunks(text):
    """[(chunk, gap_ms_after)]: paragraphs keep their own pause, sentences are grouped up to CHUNK_CHARS."""
    out = []
    for para in [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]:
        sentences = re.findall(r"[^.!?。！？]+[.!?。！？]*[\"'”’)]*\s*", " ".join(para.split())) or [para]
        cur = ""
        for s in sentences:
            if cur and len(cur) + len(s) > CHUNK_CHARS:
                out.append([cur.strip(), SENTENCE_GAP_MS])
                cur = ""
            cur += s
        if cur.strip():
            out.append([cur.strip(), SENTENCE_GAP_MS])
        out[-1][1] = None          # paragraph end: the job's pause_ms
    return out


def write_wav(path, pcm):
    tmp = path.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.astype(np.int16).tobytes())
    tmp.replace(path)


def read_wav(path):
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def progress(job_dir, step, total):
    (job_dir / "progress.json").write_text(json.dumps({"stage": "speech", "step": step, "total": total}))


def synth(spec):
    job_dir = Path(spec["job_dir"])
    ckpt = job_dir / "ckpt"
    ckpt.mkdir(parents=True, exist_ok=True)
    parts = chunks(spec["text"])
    tts = None
    for i, (text, _) in enumerate(parts):
        seg = ckpt / f"speech_{i:04d}.wav"
        if seg.exists():
            continue
        if (job_dir / "PAUSE").exists():
            return {"state": "paused"}
        progress(job_dir, i, len(parts))
        tts = tts or model(spec["paths"])
        torch.manual_seed(spec["seed"] * 1000 + i)
        np.random.seed((spec["seed"] * 1000 + i) % 2**32)
        res = tts.infer(spec["voice"], text, None, spec["lang"])
        if not res:
            return {"state": "failed", "error": f"IndexTTS produced no audio for chunk {i}: {text[:80]!r}"}
        sr, pcm = res
        if sr != SR:
            return {"state": "failed", "error": f"unexpected sample rate {sr}"}
        write_wav(seg, np.asarray(pcm).reshape(-1))
    audio = []
    for i, (_, gap) in enumerate(parts):
        audio.append(read_wav(ckpt / f"speech_{i:04d}.wav"))
        if i < len(parts) - 1:
            audio.append(np.zeros(int(SR * (spec["pause_ms"] if gap is None else gap) / 1000), dtype=np.int16))
    pcm = np.concatenate(audio)
    rel = f'{spec["out"]}/speech.wav'
    out = Path("/jobs") / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    write_wav(out, pcm)
    progress(job_dir, len(parts), len(parts))
    return {"state": "succeeded", "outputs": [rel], "duration_s": round(len(pcm) / SR, 2), "chunks": len(parts)}


def main():
    for line in sys.stdin:
        try:
            msg = json.loads(line)
            reply = synth(msg) if msg.get("cmd") == "synth" else {"state": "failed", "error": "unknown command"}
        except Exception as e:     # a failed job is a reply, not a dead worker
            traceback.print_exc()
            reply = {"state": "failed", "error": f"{type(e).__name__}: {e}"[:1000]}
        PROTO.write(json.dumps(reply) + "\n")


if __name__ == "__main__":
    main()
