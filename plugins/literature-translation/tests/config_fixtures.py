"""Explicit v7 fixture setup for domain tests formerly using a mutable project view.

This is test data construction, not a runtime compatibility layer. Deliberately
historical manifests and replacement PDFs are seeded directly for rejection tests.
"""
from pathlib import Path

from littrans.configuration import read_settings, yaml_read, yaml_text
from littrans.storage import atomic_write_text, write_yaml
from littrans.storage import save_project as save_state


def save_project(root: Path, config) -> None:
    if not (root / "project.yaml").exists():
        save_state(root, config)
    if config.schema_version != 7:
        write_yaml(root / "project.yaml", config.model_dump(mode="json", exclude_none=True))
        return
    settings = read_settings(root)
    for field in type(settings.document).model_fields:
        setattr(settings.document, field, getattr(config, field))
    for host, roles in config.agent_models.items():
        from littrans.settings import DispatchOverride
        settings.agents[host].roles = {
            key: DispatchOverride(**value.model_dump()) for key, value in roles.items()
        }
    external = config.external_review
    local_path = root / "settings.local.yaml"
    local = yaml_read(local_path) if local_path.exists() else {"schema_version": 1, "source_path": None, "commands": {}}
    if external is not None:
        from littrans.settings import ExternalSettings
        reviewers = [external.reviewer, *external.fallbacks]
        settings.external_review = ExternalSettings(
            enabled=external.enabled,
            reviewers={item.id: {key: getattr(item, key) for key in ("driver", "model", "model_identity", "effort")}
                       for item in reviewers},
            primary=external.reviewer.id, fallbacks=[item.id for item in external.fallbacks],
            recheck=external.recheck.model_dump(mode="json"), domain_expertise=external.domain_expertise, timeout_seconds=330)
        from littrans.configuration import COMMANDS
        local["commands"] = {item.id: item.command for item in reviewers if item.command != COMMANDS[item.driver]}
    elif config._settings is not None:
        settings.external_review.enabled = False
    manifest = yaml_read(root / "project.yaml")
    manifest.update(source_sha256=config.source_sha256, source_pages=config.source_pages,
                    source_path=config.source_path)
    if local.get("source_path"):
        local["source_path"] = config.source_path
    atomic_write_text(root / "project.yaml", yaml_text(manifest))
    atomic_write_text(root / "settings.yaml", yaml_text(settings.payload()))
    atomic_write_text(local_path, yaml_text(local))
    # Synthetic source replacements must be seeded before the production state
    # writer validates their identity. Production callers cannot replace a PDF.
    save_state(root, config)
