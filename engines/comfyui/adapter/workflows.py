"""API-format ComfyUI graphs behind the adapter's OpenAI-style endpoints. Filled in from /object_info."""
MODELS = ["flux2-dev", "ltx-2.5", "qwen-image-layered"]


class WorkflowError(Exception):
    pass


def build_image(req, refs, seed):
    raise WorkflowError("image workflow not built yet")


def build_video(req, image, seed):
    raise WorkflowError("video workflow not built yet")
