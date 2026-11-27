"""What a judge call receives, decided once for every adapter (FR-014 4h step B).

A validator judges an artifact against its spec. For SAD the template became
a source of rule misattribution: the 2026-11-08 spec-over-template ruling
says the spec wins, sad-validator.md never listed the template as a Required
Input, and integration_contracts did not move across two prompt iterations
with it present. So SAD judge calls go without it. Generation keeps it.

Every adapter's request builder calls :func:`template_for`, so production
validation and calibration lose the template together. A per-type set keeps
the change to SAD, the only validator with a gold set to measure it
(decision 2026-11-15, section 4h Q3).
"""

from __future__ import annotations

from src.models import AgentRequest, LifecycleEvent

# Events whose requests are judge calls: validation and lens review.
_JUDGE_EVENTS = frozenset({LifecycleEvent.PRE_VALIDATION, LifecycleEvent.POST_VALIDATION})

# Artifact types whose judge calls go without the template.
_WITHHOLD_TEMPLATE_FROM_JUDGE = frozenset({"SAD"})


def template_for(request: AgentRequest) -> str:
    """The template text this request should carry; empty when withheld."""
    if (
        request.event in _JUDGE_EVENTS
        and request.artifact_type.upper() in _WITHHOLD_TEMPLATE_FROM_JUDGE
    ):
        return ""
    return request.template_content


def template_withheld(request: AgentRequest) -> bool:
    """True when :func:`template_for` drops a template the request carried."""
    return bool(request.template_content) and not template_for(request)
