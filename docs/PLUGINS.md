# Writing a GaleQEA plugin

A plugin is a directory with a manifest and a Python module. Plugins can add
reporters, analyzers, step actions, integrations, model providers and UI panels.

## Manifest

```json
{
  "slug": "slack-reporter",
  "name": "Slack Reporter",
  "version": "1.0.0",
  "kind": "reporter",
  "entrypoint": "plugin.py",
  "sdk_version": "1.0",
  "description": "Posts a run summary to Slack.",
  "permissions": ["read:runs", "network:outbound"]
}
```

`kind` is one of `reporter`, `analyzer`, `step_action`, `integration`,
`model_provider`, `ui_panel`.

## Implementation

```python
from galeqea.plugins.sdk import PluginContext, Reporter

class SlackReporter(Reporter):
    def setup(self, ctx: PluginContext) -> None:
        ctx.require("network:outbound")   # raises if the capability was not granted
        self.http = ctx.service("http")

    def on_run_finished(self, run, results, ctx):
        failed = [r for r in results if r["status"] in ("failed", "error")]
        self.http.post(WEBHOOK, json={
            "text": f"Run #{run['number']}: {run['status']} — {len(failed)} failure(s)"
        })

plugin = SlackReporter   # a class, an instance, or a zero-argument factory
```

## Install and enable

```bash
galeqea plugins --install ./slack-reporter
galeqea plugins --enable slack-reporter --grant read:runs,network:outbound
```

Plugins install **disabled**. An admin grants each capability explicitly, and you
cannot grant a capability the manifest never requested.

## Capabilities

| Capability | Grants |
|---|---|
| `read:tests` | read test cases and their steps |
| `read:runs` | read runs and results |
| `read:requirements` | read ingested requirements |
| `write:proposals` | propose changes — still subject to the human gate |
| `network:outbound` | make outbound HTTP requests |
| `fs:artifacts` | read artifact files produced by runs |
| `ui:panel` | contribute a panel to the web UI |

Only the services matching granted capabilities appear in the context. Anything not
granted is **absent**, not merely discouraged.

## Sandboxing: an honest limitation

The sandbox constrains what a *cooperative* plugin can reach and makes an
*uncooperative* one obvious. In-process Python cannot be a true security boundary —
a determined plugin can import whatever it likes. GaleQEA therefore:

- installs plugins disabled, with no capabilities;
- requires an admin to grant each capability;
- records the entrypoint's checksum and **revokes all grants** if the code changes,
  so new code never runs under an old approval;
- states this limitation in the UI rather than implying isolation it does not have.

Run untrusted plugins out of process. The `external` transport is reserved for that
and is not yet implemented.

## Hot reload

Re-running `galeqea plugins --install` on a changed directory updates the record.
The next `load()` re-imports the module — but if the checksum changed, the plugin is
disabled until an admin re-grants.
