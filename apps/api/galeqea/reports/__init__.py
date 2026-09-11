"""AI-readable reports: one source of truth per report, three renderings.

See ``common.py`` for the shared JSON / Markdown / JUnit contract. Each sibling
module builds one report type and renders it; ``api.routes.reports`` serves them
at stable, extension-selected URLs.
"""
