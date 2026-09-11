# Sub-processors

GaleQEA itself is self-hosted software; the vendor is not a processor of your data.
The only third parties that can receive data are ones **you** configure; with the
default `no_ai`, offline configuration there are none.

| Sub-processor | When engaged | Data shared | How to avoid |
| --- | --- | --- | --- |
| Your chosen **LLM provider** (Anthropic, OpenAI, Google, Azure OpenAI, …) | only when a hosted model is configured | prompts + discovered page context for planning/healing | run a **local** model (Ollama / any OpenAI-compatible endpoint) or `no_ai` |
| Your **object storage** (if using external S3: AWS, R2, Wasabi…) | only with `storage_backend=s3` pointed at a managed endpoint | test artifacts (screenshots/video/trace/logs) | use the bundled SeaweedFS or local disk |
| Your **identity provider** (if OIDC SSO configured) | at login | the OIDC claims you release | use built-in password auth |
| Your **container registry / CI** (GHCR, GitHub Actions) | build & distribution of the images you run | source + build metadata | build and host images yourself |

GaleQEA sends **no** analytics or telemetry to the project maintainers. Telemetry is
off by default and cannot be enabled in an air-gapped deployment.
