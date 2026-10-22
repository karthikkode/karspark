# Adds the karspark gateway key to every urllib request the vendored harness makes, so the upstream
# files stay byte-identical to their pinned commit.
import os
import urllib.request

_key = os.environ.get("KARSPARK_API_KEY")
if _key:
    _Request = urllib.request.Request

    class Request(_Request):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.add_header("Authorization", f"Bearer {_key}")

    urllib.request.Request = Request
