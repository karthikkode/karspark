# Engine: hf-cli (model downloads)

A venv at `~/engines/hf-cli` with `huggingface_hub` **2.2.0**, used only by `scripts/fetch-model.sh`.

Rebuild it:

```bash
ssh spark 'python3 -m venv ~/engines/hf-cli && ~/engines/hf-cli/bin/pip install huggingface_hub==2.2.0'
```

Always download with `HF_HUB_DISABLE_XET=1`, because Xet transfers stall on the Spark's Wi-Fi, and always
pin a revision.
