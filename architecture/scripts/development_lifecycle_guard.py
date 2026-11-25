#!/usr/bin/env python3
"""Validate shared development lifecycle stages and template contracts."""

from __future__ import annotations

from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("FAIL: PyYAML is required (import yaml)")
    raise SystemExit(1)

from _env import PROJECT_ROOT  # noqa: E402


REGISTRY = PROJECT_ROOT / "docs" / "lifecycle" / "DEVELOPMENT-LIFECYCLE.yaml"
GUIDE = PROJECT_ROOT / "docs" / "lifecycle" / "DEVELOPMENT-LIFECYCLE.md"
APPROVAL_HEADINGS = (
    "## Approval",
    "## Baseline approval",
    "## Review and approval",
    "## Acceptance and sign-off",
    "## Sign-off and next action",
    "## Quality gate and approval",
)
# Stages after operate close the Measure -> Learn -> backlog loop; they must
# name who runs them and what goes in and out, not only a gate.
LOOP_STAGES = ("measure", "learn")


def rel(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def main() -> int:
    print("=" * 70)
    print("Solo Empire — Development Lifecycle Guard")
    print("=" * 70)
    errors: list[str] = []

    for path in (REGISTRY, GUIDE):
        if not path.is_file():
            errors.append(f"missing required file: {rel(path)}")
    if errors:
        for error in errors:
            print(f"  FAIL: {error}")
        return 1

    try:
        data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        print(f"  FAIL: cannot read registry: {exc}")
        return 1

    if not isinstance(data, dict) or data.get("kind") != "development-lifecycle-template-registry":
        errors.append("registry kind must be development-lifecycle-template-registry")
    stages = data.get("stages") if isinstance(data, dict) else None
    standard = data.get("standard") if isinstance(data, dict) else None
    if not isinstance(stages, list) or len(stages) < 8:
        errors.append("stages must declare at least eight lifecycle stages")
        stages = []
    if not isinstance(standard, dict):
        errors.append("standard must be a mapping")
        standard = {}
    required_sections = standard.get("required_sections", [])
    if not isinstance(required_sections, list) or not required_sections:
        errors.append("standard.required_sections must be a non-empty list")

    stage_ids: set[str] = set()
    template_paths: set[str] = set()
    for index, stage in enumerate(stages, 1):
        if not isinstance(stage, dict):
            errors.append(f"stages[{index}] must be a mapping")
            continue
        stage_id = stage.get("id")
        if not isinstance(stage_id, str) or not stage_id.strip():
            errors.append(f"stages[{index}] missing id")
        elif stage_id in stage_ids:
            errors.append(f"duplicate stage id: {stage_id}")
        else:
            stage_ids.add(stage_id)
        if not isinstance(stage.get("name"), str) or not stage["name"].strip():
            errors.append(f"stage {stage_id or index}: missing name")
        if not isinstance(stage.get("gate"), str) or not stage["gate"].strip():
            errors.append(f"stage {stage_id or index}: missing gate")
        for field in ("inputs", "outputs"):
            values = stage.get(field)
            required = stage_id in LOOP_STAGES
            if (required or values is not None) and (
                not isinstance(values, list) or not values or not all(isinstance(v, str) and v.strip() for v in values)
            ):
                errors.append(f"stage {stage_id or index}: {field} must be a non-empty list of strings")
        owner = stage.get("owner")
        if (stage_id in LOOP_STAGES or owner is not None) and (not isinstance(owner, str) or not owner.strip()):
            errors.append(f"stage {stage_id or index}: missing owner")
        templates = stage.get("templates")
        if not isinstance(templates, list) or not templates:
            errors.append(f"stage {stage_id or index}: templates must be non-empty")
            continue
        for template in templates:
            if not isinstance(template, str) or not template.startswith("templates/"):
                errors.append(f"stage {stage_id or index}: invalid template path {template!r}")
                continue
            if template in template_paths:
                # Reuse across lifecycle gates is valid and intentional.
                continue
            template_paths.add(template)
            path = PROJECT_ROOT / template
            if not path.is_file():
                errors.append(f"missing lifecycle template: {template}")
                continue
            text = path.read_text(encoding="utf-8")
            if "## Document control" not in text:
                errors.append(f"template missing document control: {template}")
            if not any(heading in text for heading in APPROVAL_HEADINGS):
                errors.append(f"template missing approval/acceptance section: {template}")

    ids = [s.get("id") for s in stages if isinstance(s, dict)]
    if "operate-and-improve" in ids:
        after = ids[ids.index("operate-and-improve") + 1 : ids.index("operate-and-improve") + 3]
        if after != list(LOOP_STAGES):
            errors.append("stages measure and learn must follow operate-and-improve in that order")

    guide_text = GUIDE.read_text(encoding="utf-8")
    for stage in stages:
        if isinstance(stage, dict) and stage.get("name") not in guide_text:
            errors.append(f"guide missing lifecycle stage: {stage.get('name')}")
    for template in template_paths:
        guide_path = f"../{template}"
        if template not in guide_text and guide_path not in guide_text:
            errors.append(f"guide does not reference lifecycle template: {template}")

    if errors:
        for error in errors:
            print(f"  FAIL: {error}")
        print(f"Result: FAIL ({len(errors)} errors)")
        return 1

    print(f"  ✅ lifecycle stages: {len(stages)}")
    print(f"  ✅ unique lifecycle templates: {len(template_paths)}")
    print("  ✅ every template has document control and approval/acceptance")
    print("  ✅ guide covers every registered stage and template")
    print("Result: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
