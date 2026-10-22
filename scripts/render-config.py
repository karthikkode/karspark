#!/usr/bin/env python3
"""Render the gateway config: gateway/config.base.yaml + gateway/routing.yaml + recipes/*/*.yaml.

Prints the merged llama-swap config to stdout, or fails loudly when a recipe breaks a karspark rule.
    scripts/render-config.py > /tmp/config.yaml
"""
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
errors = []


def load(p):
    with open(p) as f:
        return yaml.safe_load(f) or {}


config = load(ROOT / "gateway/config.base.yaml")
config.update(load(ROOT / "gateway/routing.yaml"))
models = {}
names = set()

for path in sorted(ROOT.glob("recipes/*/*.yaml")):
    folder = path.parent.name
    for mid, m in (load(path).get("models") or {}).items():
        where = f"{path.relative_to(ROOT)}: {mid}"
        if mid in models:
            errors.append(f"{where}: duplicate model id")
        if not mid.startswith(folder + ":"):
            errors.append(f"{where}: id must be '<folder>:<recipe>' ({folder}:...)")
        cmd = m.get("cmd", "")
        if "${PORT}" in cmd and "127.0.0.1:${PORT}" not in cmd:
            errors.append(f"{where}: backend must bind 127.0.0.1:${{PORT}}, never all interfaces")
        if "docker" in cmd and not m.get("cmdStop"):
            errors.append(f"{where}: docker recipes need cmdStop")
        if re.search(r"(api[-_]?key|token)\s*[=:]?\s*[A-Za-z0-9_\-]{20,}", cmd, re.I):
            errors.append(f"{where}: looks like a literal secret in cmd")
        if re.search(r"@sha256:|karspark/", cmd) is None and "docker" in cmd:
            errors.append(f"{where}: pin docker images by digest (or use a karspark/ image built here)")
        for n in m.get("aliases", []):
            if n in names or n in models:
                errors.append(f"{where}: alias '{n}' already used")
            names.add(n)
        models[mid] = m

for mid in models:
    if mid in names:
        errors.append(f"{mid}: model id also used as an alias")

# Every id named in routing sets must exist.
sets = (config.get("routing", {}).get("router", {}).get("settings", {})
        .get("matrix", {}).get("sets", {}))
mvars = (config.get("routing", {}).get("router", {}).get("settings", {}).get("matrix", {}).get("vars") or {})
for v, target in mvars.items():
    if target not in models:
        errors.append(f"matrix var '{v}' points at unknown recipe '{target}'")
known = set(models) | set((config.get("routing", {}).get("router", {}).get("settings", {})
                           .get("matrix", {}).get("vars") or {}))
for sname, expr in sets.items():
    if ":" in expr:
        errors.append(f"routing set '{sname}': use a var from matrix.vars, ':' is not allowed in set expressions")
    for token in re.findall(r"[A-Za-z0-9_.\-]+", expr):
        if token not in known and not token.startswith("auto"):
            errors.append(f"routing set '{sname}': unknown model '{token}'")

if errors:
    print("render-config: refusing to render\n  " + "\n  ".join(errors), file=sys.stderr)
    sys.exit(1)

config["models"] = models
sys.stdout.write("# RENDERED by karspark scripts/render-config.py. Do not edit on the Spark.\n")
yaml.safe_dump(config, sys.stdout, sort_keys=False, width=200)
