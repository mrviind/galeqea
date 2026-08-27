# QE Agent API

The Python backend for [QE Agent](https://github.com/mrviind/qe-agent) — an AI-first,
local-first, open-source test-automation platform.

This package hosts the FastAPI application, the canonical tool registry (shared by
the built-in chat, the MCP server, and every provider adapter), the structural
approval gate, the execution supervisor, the tiered locator-healing engine, the
App Model, and the hash-chained audit ledger.

It is installed as part of the full project — see the
[repository README](https://github.com/mrviind/qe-agent#readme) for setup
(`make start`), architecture, and usage. To work on just the API:

```bash
pip install -e "apps/api[dev]"
python -m pytest -q
```

Licensed under Apache-2.0.
