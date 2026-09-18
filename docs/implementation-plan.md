# Initial implementation plan

1. Build fixture-driven list/detail parsing and the site session adapter.
2. Build SQLite storage, resumable synchronization, and integrity checks.
3. Build the CLI, Windows scheduler helpers, and read-only stdio MCP tools.
4. Run unit tests, static checks, package build, and a bounded live sample.

Constraints: start at 2026-01-01, follow discovered pagination links, crawl
sequentially and politely, preserve empty-body metadata, ignore attachments and
images, and never make MCP queries access the remote site.

