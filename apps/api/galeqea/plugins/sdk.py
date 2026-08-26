"""Plugin SDK.

A plugin declares a manifest, requests capabilities, and is loaded into a
restricted namespace. The sandbox is honest about what it is: it constrains what
a *cooperative* plugin can reach and makes an *uncooperative* one obvious, but
in-process Python cannot be a true security boundary. So plugins are disabled on
install, an admin must grant each capability explicitly, and the risk is stated
plainly in the UI rather than papered over. Untrusted plugins belong in a
separate process - the ``external`` transport exists for exactly that.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

SDK_VERSION = "1.0"

#: Capabilities a plugin may request. Anything not granted is absent from its
#: namespace rather than merely discouraged.
CAPABILITIES = {
    "read:tests": "read test cases and their steps",
    "read:runs": "read runs and results",
    "read:requirements": "read ingested requirements",
    "write:proposals": "propose changes (still subject to human approval)",
    "network:outbound": "make outbound HTTP requests",
    "fs:artifacts": "read artifact files produced by runs",
    "ui:panel": "contribute a panel to the web UI",
}


@dataclass(slots=True)
class PluginManifest:
    slug: str
    name: str
    version: str
    kind: str                     # reporter|integration|model_provider|step_action|analyzer|ui_panel
    entrypoint: str
    sdk_version: str = SDK_VERSION
    description: str = ""
    author: str = ""
    homepage: str = ""
    permissions: list[str] = field(default_factory=list)
    config_schema: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict) -> PluginManifest:
        missing = [k for k in ("slug", "name", "version", "kind", "entrypoint") if not payload.get(k)]
        if missing:
            raise ValueError(f"manifest is missing required field(s): {', '.join(missing)}")
        unknown = [p for p in payload.get("permissions", []) if p not in CAPABILITIES]
        if unknown:
            raise ValueError(
                f"manifest requests unknown capabilities: {', '.join(unknown)}. "
                f"Valid: {', '.join(sorted(CAPABILITIES))}"
            )
        if payload.get("sdk_version", SDK_VERSION).split(".")[0] != SDK_VERSION.split(".")[0]:
            raise ValueError(
                f"plugin targets SDK {payload['sdk_version']}, this host provides {SDK_VERSION}"
            )
        return cls(**{k: v for k, v in payload.items() if k in cls.__dataclass_fields__})

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


class PluginContext:
    """The only surface a plugin may touch. Methods absent = capability not granted."""

    def __init__(self, *, granted: list[str], project_id: str, services: dict[str, Any]):
        self._granted = set(granted)
        self.project_id = project_id
        self._services = services

    def can(self, capability: str) -> bool:
        return capability in self._granted

    def require(self, capability: str) -> None:
        if capability not in self._granted:
            raise PermissionError(
                f"this plugin was not granted '{capability}' "
                f"({CAPABILITIES.get(capability, 'unknown capability')})"
            )

    def service(self, name: str) -> Any:
        service = self._services.get(name)
        if service is None:
            raise PermissionError(f"service '{name}' is not available to plugins")
        return service

    def log(self, message: str) -> None:
        print(f"[plugin] {message}")  # noqa: T201 - surfaced in the host's log stream


class GaleQEAPlugin(abc.ABC):
    """Base class every plugin subclasses."""

    manifest: PluginManifest

    # These are optional lifecycle hooks, not contract methods: a reporter that
    # needs no setup should not be forced to write an empty override.
    def setup(self, ctx: PluginContext) -> None:  # noqa: B027
        """Called once when the plugin is enabled."""

    def teardown(self) -> None:  # noqa: B027
        """Called when the plugin is disabled or hot-reloaded."""


class Reporter(GaleQEAPlugin):
    @abc.abstractmethod
    def on_run_finished(self, run: dict, results: list[dict], ctx: PluginContext) -> None: ...


class Analyzer(GaleQEAPlugin):
    @abc.abstractmethod
    def analyze(self, run: dict, results: list[dict], ctx: PluginContext) -> dict: ...


class StepAction(GaleQEAPlugin):
    @property
    @abc.abstractmethod
    def action_name(self) -> str: ...

    @abc.abstractmethod
    def to_runner_step(self, step: dict, ctx: PluginContext) -> dict:
        """Translate a custom step into runner primitives."""


class ModelProvider(GaleQEAPlugin):
    @abc.abstractmethod
    def build(self, config: dict): ...
