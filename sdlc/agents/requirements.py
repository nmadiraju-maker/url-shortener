"""Requirements Agent: interprets intent, flags ambiguity, normalises into user stories + ACs."""
from __future__ import annotations

from typing import Any

from .base import Agent, AgentContext, StageResult
from .catalog import FEATURES, NFRS, VAGUE_TERMS


def match_features(text: str) -> list[str]:
    low = text.lower()
    return [key for key, f in FEATURES.items() if any(k in low for k in f["keywords"])]


def detect_ambiguities(text: str) -> list[dict[str, Any]]:
    low = text.lower()
    return [{"term": term, **meta} for term, meta in VAGUE_TERMS.items() if term in low]


def render_markdown(req: dict[str, Any]) -> str:
    out = [f"# Requirements — {req['title']}", "", f"> Source requirement: _{req['source']}_", "",
           "## Problem statement", req["problem_statement"], ""]
    if req["ambiguities"]:
        out += ["## Ambiguities & resolutions", "| Term | Clarifying question | Resolution | Resolved by |",
                "|---|---|---|---|"]
        out += [f"| {a['term']} | {a['question']} | {a['resolution']} | {a['resolved_by']} |"
                for a in req["ambiguities"]]
        out.append("")
    out += ["## User stories"]
    for s in req["stories"]:
        tag = {"bug": ", bug", "regression": ", existing — regression only"}.get(s["kind"], "")
        out += ["", f"### {s['id']} ({s['priority']}{tag}) — {s['feature']}",
                f"**As a** {s['as_a']}, **I want to** {s['i_want']}, **so that** {s['so_that']}.", "",
                "| AC | Given | When | Then |", "|---|---|---|---|"]
        out += [f"| {ac['id']} | {ac['given']} | {ac['when']} | {ac['then']} |" for ac in s["acceptance_criteria"]]
    out += ["", "## Non-functional requirements"] + [f"- **{n['id']}** {n['text']}" for n in req["nfrs"]]
    out += ["", "## Out of scope"] + [f"- {o}" for o in req["out_of_scope"]]
    return "\n".join(out) + "\n"


class RequirementsAgent(Agent):
    name = "requirements"

    def run(self, ctx: AgentContext) -> StageResult:
        text: str = ctx.params["requirement"]
        clarifications: dict[str, Any] = ctx.params.get("clarifications", {})
        features = match_features(text)
        ambiguities, assumed = [], []
        for amb in detect_ambiguities(text):
            clar = clarifications.get(amb["term"])
            if clar is not None:
                chosen = clar if clar in FEATURES else None
                by, resolution = "human clarification", (FEATURES[clar]["story"][1] if chosen else clar)
            elif amb["options"]:
                chosen, by = amb["options"][0], "agent assumption (needs sign-off)"
                resolution = FEATURES[chosen]["story"][1]
                assumed.append(amb["term"])
            else:
                chosen, by, resolution = None, "unresolved", "needs human input"
            ambiguities.append({"term": amb["term"], "question": amb["question"], "resolution": resolution,
                                "feature": chosen, "resolved_by": by})
            if chosen and chosen not in features:
                features.append(chosen)
        existing = set(ctx.params.get("existing_features", []))
        stories = []
        for i, key in enumerate(features, 1):
            f = FEATURES[key]
            as_a, want, so_that = f["story"]
            stories.append({"id": f"US-{i:02d}", "feature": key, "as_a": as_a, "i_want": want, "so_that": so_that,
                            "priority": f["priority"],
                            "kind": "regression" if key in existing else f.get("kind", "feature"),
                            "acceptance_criteria": [
                                {"id": f"AC-{f['ac_prefix']}-{n}", "given": g, "when": w, "then": t}
                                for n, (g, w, t) in enumerate(f["ac"], 1)]})
        unresolved = [a["term"] for a in ambiguities if a["resolved_by"] == "unresolved"]
        req = {"title": ctx.params.get("title", "URL shortener"), "source": text,
               "problem_statement": ctx.params.get("problem_statement",
                                                   "Provide a reliable, secure URL shortening service with analytics."),
               "features": features, "stories": stories, "ambiguities": ambiguities, "nfrs": NFRS,
               "out_of_scope": ctx.params.get("out_of_scope", ["User accounts/OAuth (API key + owner header only)",
                                                              "Geo-IP analytics", "Multi-region replication"])}
        req["markdown"] = render_markdown(req)
        return StageResult(
            summary=f"{len(stories)} stories, {sum(len(s['acceptance_criteria']) for s in stories)} ACs, "
                    f"{len(ambiguities)} ambiguities ({len(assumed)} assumed, {len(unresolved)} unresolved)",
            artifacts={"requirements": req},
            checks={"stories_have_acceptance_criteria": bool(stories) and all(
                        s["acceptance_criteria"] for s in stories),
                    "no_unresolved_ambiguity": not unresolved},
            feedback=[f"Unresolved ambiguity '{t}': ask the requester" for t in unresolved],
            escalate="; ".join(filter(None, [
                f"assumptions made for {assumed}; requester must confirm" if assumed else "",
                f"unresolved ambiguity {unresolved}: requester must clarify" if unresolved else ""])) or None,
            decisions=[{"summary": f"Interpret '{a['term']}' as '{a['feature']}'",
                        "rationale": a["resolved_by"],
                        "alternatives": [o for o in VAGUE_TERMS[a['term']]["options"] if o != a["feature"]]}
                       for a in ambiguities if a["feature"]])

