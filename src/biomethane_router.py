"""
biomethane_router.py — Deterministic Intent Routing Layer
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SOURING_WATCH  = 0.10
_SOURING_ACTION = 0.30
_OLR_HIGH       = 3.5

_INTENT_RULES: list[tuple[str, list[str]]] = [
    ("SOURING", [
        "souring", "sour", "vfa", "fos", "fos/tac", "fos tac",
        "acidif", "volatile fatty",
        "digester health", "digester risk", "digester stable",
        "methanogens", "methanogen",
        "x2", "s2",
        "risk", "danger zone", "headroom",
        "washout", "wash out",
    ]),
    ("SCENARIO", [
        "what if", "what happens if", "what would happen",
        "double", "triple", "increase feed", "boost feed",
        "overload", "spike", "surge",
        "blend", "blending", "feedstock mix", "recipe",
        "olr", "organic loading",
        "can i add", "can i increase", "can i feed",
        "food waste", "maize", "cattle slurry", "silage",
        "safe to", "is it safe",
    ]),
    ("PRODUCTION", [
        "kpi", "kpis", "production", "revenue", "yield",
        "how much methane", "how much biomethane",
        "output", "throughput", "efficiency",
        "daily report", "weekly report", "monthly report",
        "grid spec", "grid injection", "purity",
        "target", "compliance",
    ]),
    ("REFERENCE", [
        "what causes", "why is", "why does", "explain",
        "how does", "how do i", "what is", "what are",
        "mesophilic", "thermophilic",
        "h2s", "hydrogen sulphide", "hydrogen sulfide",
        "ammonia", "inhibition", "desulphuri", "desulfuri",
        "troubleshoot", "problem", "issue",
        "temperature range", "safe range", "limit",
        "difference between",
    ]),
    ("STATUS", [
        "status", "state", "how is", "how are",
        "current reading", "current value",
        "alarm", "alert", "warning",
        "temperature", "ph", "biogas flow",
        "plant summary", "plant report", "morning briefing",
        "anything wrong", "is everything", "is it ok",
        "check",
    ]),
]


def classify(message: str) -> str:
    lower = message.lower()
    for intent, keywords in _INTENT_RULES:
        if any(kw in lower for kw in keywords):
            logger.debug("Router: classified '%s...' as %s", message[:40], intent)
            return intent
    logger.debug("Router: no match for '%s...' -> UNKNOWN", message[:40])
    return "UNKNOWN"


def _call(mcp, tool: str, args: dict) -> dict:
    raw = mcp.call_tool(tool, args)
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return {"raw": str(raw)}


def _chain_status(mcp) -> tuple[str, list[str]]:
    state = _call(mcp, "get_plant_state", {})
    alerts = _call(mcp, "check_alerts", {})
    context = (
        "=== ROUTER: STATUS chain ===\n"
        f"Plant state:\n{json.dumps(state, indent=2)}\n\n"
        f"Alerts:\n{json.dumps(alerts, indent=2)}\n"
        "=== Use the above data to answer the operator. "
        "Do not call get_plant_state or check_alerts again. ==="
    )
    return context, ["get_plant_state", "check_alerts"]


def _extract_fos(message: str) -> Optional[float]:
    """
    Extract a FOS/VFA concentration in mg/L from free text.

    Matches patterns like "4500 mg/L", "800 mg/l", "1200mg/L" when
    accompanied by FOS/VFA context words. Returns None if not found.
    """
    import re
    lower = message.lower()
    if not any(w in lower for w in ["fos", "vfa", "volatile", "tac"]):
        return None
    match = re.search(r'(\d+(?:\.\d+)?)\s*mg\s*/?\s*[lL]', message)
    if match:
        return float(match.group(1))
    return None


def _chain_souring(mcp, user_message: str = "") -> tuple[str, list[str]]:
    tools_called = []
    args: dict = {}
    fos = _extract_fos(user_message)
    if fos is not None:
        args["fos_mg_per_l"] = fos
        logger.debug("Router: FOS %.1f mg/L extracted from message — passing to enkf_update", fos)
    enkf = _call(mcp, "enkf_update", args)
    tools_called.append("enkf_update" if fos is None else f"enkf_update(fos_mg_per_l={fos})")
    souring_prob = float(enkf.get("souring_probability", 0.0))
    pert_section = ""
    if souring_prob >= _SOURING_ACTION:
        # Prefer value persisted by enkf_initialise; fall back to site_config.
        # Never use a magic number — wrong scale gives meaningless simulation.
        s1_in = float(enkf.get("S1_in_g_per_L", _S1_IN_G_PER_L))
        pert = _call(mcp, "ad4_perturbation_test", {
            "overload_cod_g_per_l": s1_in,
            "overload_days":        10.0,
            "recovery_days":        30.0,
        })
        tools_called.append("ad4_perturbation_test")
        pert_section = (
            f"\nPerturbation test (current COD {s1_in} g/L, 10-day horizon):\n"
            f"{json.dumps(pert, indent=2)}\n"
        )
    elif souring_prob >= _SOURING_WATCH:
        pert_section = (
            f"\nSouring probability ({souring_prob:.2f}) is in WATCH range "
            f"({_SOURING_WATCH}-{_SOURING_ACTION}). "
            "No perturbation test needed yet - advise operator to monitor trend.\n"
        )
    context = (
        "=== ROUTER: SOURING chain ===\n"
        f"EnKF estimate:\n{json.dumps(enkf, indent=2)}\n"
        f"{pert_section}"
        "INSTRUCTIONS FOR LLM: "
        "State S2 estimate and uncertainty. "
        "State souring_probability and risk_level in plain language. "
        "Never use the words 'EnKF', 'AD4', or 'perturbation test' when speaking to the operator. "
        "If perturbation results are present, describe the headroom in plain language. "
        "Do not call enkf_update or ad4_perturbation_test again. ==="
    )
    return context, tools_called


def _chain_scenario(mcp, user_message: str) -> tuple[str, list[str]]:
    state = _call(mcp, "get_plant_state", {})
    context = (
        "=== ROUTER: SCENARIO chain ===\n"
        f"Current plant state (for temperature/HRT context):\n"
        f"{json.dumps(state, indent=2)}\n\n"
        f"Digester volume: {_DIGESTER_VOLUME_M3} m3 "
        f"(from site_config.estimated_geometry)\n"
        f"Baseline influent COD: {_S1_IN_G_PER_L} g/L "
        f"(from site_config.estimated_geometry)\n\n"
        "INSTRUCTIONS FOR LLM: "
        "This is a feedstock or what-if scenario question. "
        "Call blend_feedstocks with the quantities the operator mentioned, "
        f"then olr_from_recipe with digester_volume_m3={_DIGESTER_VOLUME_M3} "
        "to check organic loading rate. "
        f"If OLR > {_OLR_HIGH} kg VS/m3/day, also call ad4_perturbation_test "
        f"with overload_cod_g_per_l={_S1_IN_G_PER_L} to model the overload response. "
        "Present results in plain language without tool names. ==="
    )
    return context, ["get_plant_state"]


def _chain_production(mcp, user_message: str) -> tuple[str, list[str]]:
    period = _extract_period(user_message)
    kpis = _call(mcp, "get_kpi_summary", {"period": period})
    context = (
        "=== ROUTER: PRODUCTION chain ===\n"
        f"KPI summary (period={period}):\n{json.dumps(kpis, indent=2)}\n"
        "=== Use the above data to answer. "
        "Do not call get_kpi_summary again unless the operator asks for "
        "a different period. ==="
    )
    return context, [f"get_kpi_summary({period})"]


def _extract_period(message: str) -> str:
    """
    Extract KPI period from message. (Fix 1)
    Valid MCP periods: 'daily', 'weekly'.
    'monthly' is not supported — falls back to 'weekly'.
    Returns 'daily' if no period keyword found.
    """
    lower = message.lower()
    if any(w in lower for w in ["week", "7 day", "7-day", "last week", "weekly"]):
        return "weekly"
    return "daily"


def _map_reference_topic(message: str) -> Optional[str]:
    """
    Map message to a valid get_operational_reference topic. (Fix 2)

    Valid topics in _OPERATIONAL_REFERENCE (MCP server v5):
        fos_tac     — FOS/TAC ratio, buffering, alkalinity
        temperature — Operating temperature ranges
        olr         — Organic loading rate limits
        cn_ratio    — C/N ratio targets

    Returns None if no valid topic matches — caller falls through to
    UNKNOWN rather than receiving 'No reference found for this topic'.
    """
    lower = message.lower()
    if any(w in lower for w in ["fos", "tac", "fos/tac", "fos tac",
                                  "buffer", "alkalinity", "acidif"]):
        return "fos_tac"
    if any(w in lower for w in ["temperature", "temp", "mesophilic",
                                  "thermophilic", "degrees"]):
        return "temperature"
    if any(w in lower for w in ["organic loading", "olr", "loading rate",
                                  "kg vs", "vs/m"]):
        return "olr"
    if any(w in lower for w in ["c/n", "cn ratio", "carbon nitrogen",
                                  "carbon to nitrogen"]):
        return "cn_ratio"
    return None


def _chain_reference(mcp, user_message: str) -> tuple[str, list[str]]:
    """
    REFERENCE: operational reference lookup against valid MCP topics. (Fix 2)
    Returns ("", []) if topic mapping fails — route() falls through to UNKNOWN.
    """
    topic = _map_reference_topic(user_message)
    if topic is None:
        return "", []
    ref = _call(mcp, "get_operational_reference", {"topic": topic})
    context = (
        "=== ROUTER: REFERENCE chain ===\n"
        f"Operational reference (topic={topic}):\n"
        f"{json.dumps(ref, indent=2)}\n"
        "=== Summarise the relevant parts for the operator in plain language. "
        "Do not call get_operational_reference again. ==="
    )
    return context, [f"get_operational_reference({topic})"]


def _load_site_geometry() -> tuple[float, float]:
    """
    Load digester_volume_m3 and s1_in_g_per_l from site_config.json.

    Both values are required and have no safe silent default:
    - digester_volume_m3: wrong by orders of magnitude between lab and industrial scale.
    - s1_in_g_per_l: used directly in perturbation test COD — wrong value gives
      a physically meaningless overload simulation.

    Raises RuntimeError if the file is missing or either key is absent.
    Add both keys to estimated_geometry in site_config.json before running.
    """
    candidates = [
        Path(__file__).parent / "site_config.json",
        Path(__file__).parent.parent / "site_config.json",
    ]
    for path in candidates:
        if path.exists():
            try:
                cfg = json.loads(path.read_text())
                geo  = cfg.get("estimated_geometry", {})
                vol  = geo.get("digester_volume_m3")
                s1in = geo.get("s1_in_g_per_l")
                missing = [k for k, v in
                           [("digester_volume_m3", vol), ("s1_in_g_per_l", s1in)]
                           if v is None]
                if missing:
                    raise RuntimeError(
                        f"site_config.json at {path} is missing "
                        f"estimated_geometry keys: {missing}. "
                        "Add them before running the router."
                    )
                logger.debug(
                    "Router: loaded digester_volume_m3=%.3f s1_in_g_per_l=%.1f from %s",
                    vol, s1in, path,
                )
                return float(vol), float(s1in)
            except RuntimeError:
                raise
            except Exception as e:
                raise RuntimeError(
                    f"Router: could not read site_config.json at {path}: {e}"
                ) from e
    raise RuntimeError(
        "Router: site_config.json not found in src/ or project root. "
        "Cannot determine digester geometry — router will not start."
    )


_DIGESTER_VOLUME_M3: float
_S1_IN_G_PER_L: float
_DIGESTER_VOLUME_M3, _S1_IN_G_PER_L = _load_site_geometry()

def route(
    user_message: str,
    mcp,
) -> tuple[Optional[str], str, list[str]]:
    """
    Classify the operator's message, run the deterministic tool chain,
    and return a context string for the LLM to synthesise.

    Returns (context, intent, tools_called).
    context is None for UNKNOWN intent — LLM handles tool selection as before.
    """
    intent = classify(user_message)

    try:
        if intent == "STATUS":
            context, tools = _chain_status(mcp)
        elif intent == "SOURING":
            context, tools = _chain_souring(mcp, user_message)
        elif intent == "SCENARIO":
            context, tools = _chain_scenario(mcp, user_message)
        elif intent == "PRODUCTION":
            context, tools = _chain_production(mcp, user_message)
        elif intent == "REFERENCE":
            context, tools = _chain_reference(mcp, user_message)
            if not context:
                # Topic mapping failed — fall through to LLM
                return None, "UNKNOWN", []
        else:
            return None, "UNKNOWN", []

    except Exception as exc:
        logger.warning("Router chain failed for intent %s: %s", intent, exc)
        return None, intent, []

    return context, intent, tools
