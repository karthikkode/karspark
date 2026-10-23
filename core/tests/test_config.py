import shutil

import pytest

from karspark import config as cfgmod
from conftest import REPO


def test_repo_config_loads(cfg):
    assert cfg.sched.default_mode == "coding"
    assert cfg.modes["coding"].coding
    assert cfg.engine_for("creative", "flux2-dev").id == "comfyui:comfy"
    assert cfg.mode_of_model("qwen3.8-flash") == ["coding"]
    assert cfg.media_engine("coding") is None
    assert "seedvr2-7b" in cfg.capability_models("upscale_image")


def _copy(tmp_path):
    for d in ("recipes", "config"):
        shutil.copytree(REPO / d, tmp_path / d, ignore=shutil.ignore_patterns("bench", "*.md"))
    return tmp_path


def test_rejects_public_port_and_unpinned_image(tmp_path):
    root = _copy(tmp_path)
    p = root / "recipes/qwen3-0.6b/vllm.yaml"
    s = p.read_text().replace("-p 127.0.0.1:${PORT}:8000", "-p ${PORT}:8000")
    s = s.replace("vllm/vllm-openai@sha256:6ff5ff0ddf3658d2c2d677dac5ca2d4726ce0156b4371166d918181b210bd4bc", "vllm/vllm-openai:latest")
    p.write_text(s)
    with pytest.raises(cfgmod.ConfigError) as e:
        cfgmod.load(root)
    assert "127.0.0.1" in str(e.value) and "pin the image" in str(e.value)


def test_rejects_mode_over_memory(tmp_path):
    root = _copy(tmp_path)
    p = root / "config/modes.yaml"
    p.write_text(p.read_text().replace('engines: ["qwen3.8-27b:vllm", "comfyui:comfy"]',
                                       'engines: ["qwen3.8-27b:vllm", "comfyui:comfy", "qwen3-0.6b:vllm"]'))
    with pytest.raises(cfgmod.ConfigError, match="limit 115"):
        cfgmod.load(root)
