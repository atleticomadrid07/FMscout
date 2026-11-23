"""Position templates (built-in for now; custom ones come with the comparison screen)."""

from __future__ import annotations

from fastapi import APIRouter

from scouting.api.errors import ERROR_RESPONSES, not_found
from scouting.api.schemas import TemplateOut, TemplateSectionOut, TemplatesOut
from scouting.domain.positions import PEER_ROLES
from scouting.metrics.templates import Template, builtin_templates

router = APIRouter(tags=["templates"], responses=ERROR_RESPONSES)


def template_out(t: Template) -> TemplateOut:
    return TemplateOut(
        id=t.id,
        peer_role=t.peer_role,
        label=t.label,
        builtin=t.builtin,
        size=len(t.codes),
        sections=[TemplateSectionOut(name=s.name, metrics=list(s.metrics)) for s in t.sections],
    )


@router.get("/templates")
def list_templates() -> TemplatesOut:
    """One built-in template per peer role, in peer-role order."""
    templates = builtin_templates()
    return TemplatesOut(
        templates=[template_out(templates[r]) for r in PEER_ROLES if r in templates]
    )


@router.get("/templates/{template_id}")
def get_template(template_id: str) -> TemplateOut:
    """By id, e.g. `builtin:ST`."""
    template = next((t for t in builtin_templates().values() if t.id == template_id), None)
    if template is None:
        raise not_found("UNKNOWN_TEMPLATE", f"no template {template_id!r}; see /api/v1/templates")
    return template_out(template)
