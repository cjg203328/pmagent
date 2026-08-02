# External Skill Plugins

External Python skills are trusted deployment code. They run in the ArtPM
process and are not a sandbox. The loader is therefore disabled by default and
requires all of these controls before it executes a module:

- an absolute operator-configured root directory;
- an explicitly allowlisted plugin ID;
- a matching module SHA-256 in `artpm-plugin.json`;
- no symbolic links or Windows junctions in the load path;
- no collision with a built-in or previously loaded skill name.

Each immediate child of a trusted root is one plugin directory. A minimal
manifest looks like this:

```json
{
  "schema_version": 1,
  "plugin_id": "studio.asset-review",
  "version": "1.0.0",
  "module": "plugin.py",
  "module_sha256": "<64 lowercase hex characters>",
  "skills": [
    {
      "name": "asset_review",
      "class": "AssetReviewSkill",
      "description": "Review an asset package",
      "version": "1.0.0",
      "risk": "low",
      "read_only": true,
      "requires_approval": false,
      "capabilities": ["asset.analyze"]
    }
  ]
}
```

The class must inherit `artpm_agent.skills.base_skill.BaseSkill`, and its
`skill_name` must match the manifest. Write-capable skills must set
`requires_approval` to `true`.

Enable plugins only in deployment environment configuration:

```dotenv
ARTPM_PLUGINS_ENABLED=true
ARTPM_PLUGIN_ROOTS=["D:\\ArtPM\\plugins"]
ARTPM_PLUGIN_ALLOWLIST=["studio.asset-review"]
```

Plugin capabilities appear in `SkillRouter.list_skills()`. A workflow host must
intersect them with its own server allowlist and risk policy; a plugin manifest
cannot grant itself workflow execution permission.

For shared cloud runtimes, derive `router.for_tenant(tenant_context)` per
request. The ingress must first authenticate tenant membership, and the
`workspace_id` placed in `TenantContext` must be globally unique in storage.
