# karspark docs

Start with the row for what you're doing.

## Using karspark (other projects, their agents)

| doc | read it when |
|---|---|
| [agents.md](agents.md) | **you are an agent calling karspark**: the rules for getting results fast, what each mode does to your request, costs, patterns, error handling, a snippet for your project's CLAUDE.md |
| [integrating.md](integrating.md) | wiring a project in: keys, tiers, priorities, text, jobs, inputs, collecting results, do/don't |
| [mcp.md](mcp.md) | connecting Claude Code or opencode to the `karspark` MCP server; every tool and resource |
| [api.md](api.md) · [openapi.json](openapi.json) | the exact HTTP routes, parameters, job object and status codes |
| [../clients/python/README.md](../clients/python/README.md) | the Python SDK (`karspark_client`) |
| [clients/opencode.md](clients/opencode.md) | opencode as a coding client on Flash-Next |

## How karspark behaves

| doc | covers |
|---|---|
| [concepts.md](concepts.md) | the vocabulary (model, recipe, engine, mode, job, tier, slot, checkpoint) and how a request flows |
| [modes.md](modes.md) | which engines load together and the five rules that switch modes |
| [scheduler.md](scheduler.md) | priority, aging, fair share, model affinity, preemption, lossless pause, ETAs, limits |
| [logging.md](logging.md) | what is recorded, for how long, and who can read it |
| [architecture.md](architecture.md) | why karspark-core exists and what it guarantees |

## Changing karspark (agents working in this repo)

| doc | covers |
|---|---|
| [../CLAUDE.md](../CLAUDE.md) | the rules for this repo and the box: read it first |
| [standard.md](standard.md) | how anything gets onto the Spark: layout, names, recipes, the add-a-model checklist |
| [lessons.md](lessons.md) | working recipes, traps and incidents. Read before adding a model or touching the box |
| [registry.md](registry.md) | what is on the box right now: modes, recipes, weights, images, ports, measured numbers |
| `recipes/<model>/README.md` | one model: weights and revision, recipes, measurements |
| [plans/](plans/) | design notes for larger changes |

## Keeping these current

A change that alters behaviour updates the docs that describe it **in the same commit**:

| you changed | update |
|---|---|
| a route, parameter or response | api.md, `scripts/export-openapi.sh` (regenerates openapi.json), integrating.md if callers see it, the SDK |
| an MCP tool or its defaults | mcp.md, the `INSTRUCTIONS` string in `core/karspark/mcp_server.py`, agents.md |
| modes, tiers, scheduler config | modes.md / scheduler.md / integrating.md (tier table), agents.md (mode table) |
| a model, recipe or image | registry.md, the recipe README, the model lists in integrating.md, mcp.md and agents.md |
| a measured number (load time, tok/s, job time) | registry.md, the recipe README, the cost table in agents.md |
| something that went wrong on the box | lessons.md |
