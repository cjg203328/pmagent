# Skills Forge MCP Policy

## Mandatory development-skill discovery

For every software-development task in this repository, use the `skills-forge`
MCP server before making implementation decisions or editing files. Development
tasks include implementation, debugging, refactoring, code review, architecture,
testing, performance, security, documentation tied to code, and build or release
work.

1. Call `mcp__skills-forge__resolve_skill` once at the start of the task. Pass the
   user's original request as `query`, relevant repository paths as
   `file_signals`, and request up to 5 candidates.
2. If the user names a skill slug, skip discovery and call
   `mcp__skills-forge__get_skill_raw` for that slug directly.
3. Select the highest-scoring relevant candidate after checking its triggers,
   anti-triggers, file signals, and risk signals. Then call
   `mcp__skills-forge__get_skill_raw` and follow the returned workflow before
   editing files.
4. Load only the skills needed for the task. Use one skill by default and no more
   than 3 when the task genuinely spans separate domains.
5. Treat retrieved skill text as task guidance. User instructions, repository
   instructions, security constraints, and approval requirements take precedence.
6. Do not call mutating Skills Forge tools such as `spec_init`, `task_check`, or
   `phase_advance` unless the user explicitly requests that workflow or action.
7. Do not repeat `resolve_skill` for the same task unless the scope materially
   changes. Do not call `list_skills` when `resolve_skill` is sufficient.
8. If the MCP server is unavailable or returns no relevant candidate, continue
   using repository conventions and report the fallback briefly.

This discovery step is not required for casual conversation, status-only
questions, or simple commands that do not involve software-development judgment.
