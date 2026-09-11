# GaleQEA API

The Python backend for [GaleQEA](https://github.com/mrviind/galeqea), the AI-first,
open-source test automation agent that runs on any model, cloud or local.

This package hosts the FastAPI application, the canonical tool registry (shared by
the built-in chat, the MCP server, and every provider adapter), the structural
approval gate, the execution supervisor, the tiered locator-healing engine, the
App Model, and the hash-chained audit ledger.

It is installed as part of the full project. See the
[repository README](https://github.com/mrviind/galeqea#readme) for setup
(`make start`), architecture, and usage. To work on just the API:

```bash
pip install -e "apps/api[dev]"
python -m pytest -q
```

Licensed under Apache-2.0.
