# CBD v2.2 Blueprint JSON Schema Reference

> This file is loaded by the self-evolution skill during Phase 4 blueprint generation.

## Full Schema

```json
{
  "project_id": "unique-project-identifier",
  "methodology": "CBD-Interface-First",
  "blueprint_version": "1.0.0",
  "blueprint_changelog": [
    {
      "version": "1.0.0",
      "date": "ISO8601-date",
      "changed_by": "agent | human",
      "summary": "Initial blueprint"
    }
  ],
  "experienced_system": {
    "enabled": true,
    "index_path": "experienced/index.md",
    "entries_path": "experienced/",
    "mandatory_lookup_before": ["debugging", "environment-setup", "dependency-resolution"]
  },
  "evolution_metadata": {
    "workspace_id": "ws-YYYYMMDD-HHMM-slug",
    "origin_path": "/path/to/original/source",
    "trigger": "Description of what triggered this evolution",
    "phases_completed": [],
    "abort_conditions_hit": []
  },
  "components": [
    {
      "identity": {
        "name": "COMPONENT_NAME",
        "reason": "Clear purpose statement",
        "logical_function": "Function category"
      },
      "change_type": "CREATED | MODIFIED | DELETED",
      "version_before": "1.0.0",
      "version_after": "1.1.0",
      "increment_type": "MAJOR | MINOR | PATCH",
      "increment_rationale": "Why this increment level was chosen",
      "interface": {
        "api": {
          "endpoint": "/api/path",
          "method": "HTTP_METHOD",
          "request_structure": {},
          "result_structure": {}
        },
        "in_schema": {},
        "out_schema": {},
        "error_schema": {}
      },
      "artifacts": [
        {
          "file_path": "path/to/file",
          "description": "Purpose of artifact",
          "type": "source | config | test | doc",
          "version": "1.0.0",
          "changelog": [
            {
              "version": "1.0.0",
              "date": "ISO8601-date",
              "summary": "Initial implementation"
            }
          ]
        }
      ],
      "adaptor_requirements": [],
      "observability": {
        "trace_points": [],
        "failure_map": []
      },
      "experience_hook": {
        "consumer": true,
        "producer": true,
        "lookup_trigger": "on_failure | always | never",
        "write_trigger": "on_novel_resolution | always | never"
      },
      "test_coverage": {
        "happy_path": true,
        "edge_cases": [],
        "error_paths": [],
        "regression_tests": []
      }
    }
  ],
  "deployment": {
    "backup_path": "workspace/reports/backup/",
    "deploy_manifest": "workspace/reports/deploy-manifest.txt",
    "restart_mechanism": "skill_reload | systemctl | docker | pm2 | process",
    "health_check_endpoint": "/health",
    "health_check_timeout_seconds": 30,
    "rollback_on_failure": true
  }
}
```

## Required Fields

Every blueprint.json MUST have:
- `project_id` — unique identifier
- `blueprint_version` — starts at `1.0.0` for new blueprints
- `blueprint_changelog` — at least the initial entry
- `evolution_metadata.workspace_id` — links to the workspace
- At least one entry in `components`
- `deployment.rollback_on_failure: true` — non-negotiable

## Forbidden Patterns

- `blueprint_version` left at a previous value without incrementing
- `components` array empty
- `deployment.rollback_on_failure: false`
- Missing `error_schema` in any component interface
