"""Service level objectives computed from recorded probes.

SLI (service level indicator) = good checks / valid checks in a rolling window.
SLO = the goal for that SLI, e.g. 99.5% over 7 days.
Error budget = the (100 - goal)% of valid checks that may be bad. Budget remaining is
100 * (1 - bad / (valid * allowed bad fraction)); below zero the SLO is breached.
Burn rate = the bad fraction in a short recent window divided by the allowed bad fraction:
1.0 spends the budget exactly over the window, 2.0 spends it twice as fast.

Unobserved time (monitor stopped) is neither good nor bad. coverage_percent reports how much
of the window the samples actually span, so a fresh install does not look like a week of data.
"""
import time

AT_RISK_BUDGET_PERCENT = 25
FAST_BURN_RATE = 2


def evaluate(slo, target, window, burn, now):
    """window and burn: Store.sli_counts results for the SLO window and the burn window."""
    allowed = 1 - slo.goal_percent / 100
    valid, good = window["valid"], window["good"]
    bad = valid - good
    observed_hours = (window["last"] - window["first"]) / 3600 if window["first"] is not None else 0
    result = {
        **slo.public(), "target_name": target.name, "plane": target.plane,
        "valid_checks": valid, "good_checks": good, "bad_checks": bad,
        "allowed_bad_fraction": allowed, "budget_checks": valid * allowed,
        # If the service were measured continuously, the budget would be this much time per window.
        "allowed_bad_minutes": slo.window_hours * 60 * allowed,
        # Each bad check stands for roughly one probe interval out of objective.
        "estimated_bad_minutes": bad * target.interval / 60,
        "observed_hours": observed_hours,
        "coverage_percent": min(100.0, 100 * observed_hours / slo.window_hours),
        "burn_valid_checks": burn["valid"], "burn_bad_checks": burn["valid"] - burn["good"],
        "attainment_percent": None, "budget_remaining_percent": None, "burn_rate": None,
        "sla_met": None, "status": "no_data", "evaluated_at": now,
    }
    if not valid:
        return result
    attainment = 100 * good / valid
    remaining = 100 * (1 - bad / (valid * allowed))
    burn_rate = (burn["valid"] - burn["good"]) / burn["valid"] / allowed if burn["valid"] else None
    if attainment < slo.goal_percent:
        status = "breached"
    elif remaining < AT_RISK_BUDGET_PERCENT or (burn_rate or 0) >= FAST_BURN_RATE:
        status = "at_risk"
    else:
        status = "met"
    result.update(attainment_percent=attainment, budget_remaining_percent=remaining, burn_rate=burn_rate,
                  sla_met=None if slo.sla_percent is None else attainment >= slo.sla_percent, status=status)
    return result


def evaluate_all(store, config, now=None):
    now = time.time() if now is None else now
    results = []
    for slo in config.slos:
        window = store.sli_counts(slo, now - slo.window_hours * 3600)
        burn = store.sli_counts(slo, now - slo.burn_window_hours * 3600)
        results.append(evaluate(slo, config.target(slo.target), window, burn, now))
    return {"generated_at": now, "at_risk_budget_percent": AT_RISK_BUDGET_PERCENT,
            "fast_burn_rate": FAST_BURN_RATE, "slos": results}
