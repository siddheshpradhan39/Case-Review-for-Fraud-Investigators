"""AI-assisted rule drafting: English -> a structured rule proposal in the SAME validated DSL the builder uses.
The model can only reference real fields; anything invalid is repaired once and otherwise rejected with the reason.
It never saves anything: the proposal lands in the builder with a live backtest for the human to review."""
import json

from .. import rulestore
from ..customrules import DOMAIN_CHOICES, LEVELS, RuleError, validate_logic
from . import client

SYSTEM = """You translate a fraud investigator's plain-English idea into ONE structured detection rule for LTC insurance claims.
Use ONLY these fields (name: type - meaning):
{fields}
Rule format (JSON only, no prose):
{{"name": "3-80 chars", "description": "one sentence", "level": one of {levels}, "domain": one of {domains},
 "logic": {{"op": "AND" or "OR", "conds": [ {{"field": "<field>", "cmp": "<op>", "value": <value>}} , ... or nested {{"op":..,"conds":[..]}} ]}}}}
Operators: numbers >= > <= < == != ; flags (0/1) == or != with 0 or 1 ; category fields (care_type, state) == != in not_in ("in" takes a list of text values).
Category values: care_type in {care_types}; state in {states}.
Pick a level that reflects how strong the evidence is (HIGH only for strong standalone indicators). Choose the domain that fits.
Example: "weekend-heavy billing with lots of round dollars" -> {{"name":"Weekend and round-dollar billing","description":"High weekend share plus round-dollar charges.","level":"MEDIUM","domain":"Billing","logic":{{"op":"AND","conds":[{{"field":"weekend_billing_ratio","cmp":">=","value":0.3}},{{"field":"round_dollar_billing_ratio","cmp":">=","value":0.4}}]}}}}
If the request cannot be expressed with these fields, reply {{"error": "why not"}}."""


async def draft(description):
    description = (description or "").strip()
    if len(description) < 8:
        raise RuleError("describe the rule in a sentence")
    cat = {f["name"]: f for f in rulestore.field_catalogue()}
    sysmsg = SYSTEM.format(fields="\n".join(f"- {f['name']}: {f['type']} - {f['label']}" for f in cat.values()), levels=LEVELS, domains=DOMAIN_CHOICES,
                           care_types=cat["care_type"]["values"], states=cat["state"]["values"])
    msgs = [{"role": "system", "content": sysmsg}, {"role": "user", "content": description}]
    last = None
    for attempt in range(2):
        msg, model = await client.chat(msgs, tier="fast", max_tokens=700, temperature=0.1)
        try:
            raw = client.extract_json(msg.get("content"))
            if raw.get("error"):
                raise RuleError("the AI could not express that with the available fields: " + str(raw["error"])[:200])
            d = rulestore.validate_definition({**raw, "status": "shadow"}, check_name=False)
            return {**d, "status": "active", "model": model}
        except RuleError as e:
            if "could not express" in str(e):
                raise
            last = str(e)
        except ValueError as e:
            last = str(e)
        msgs += [{"role": "assistant", "content": msg.get("content") or ""},
                 {"role": "user", "content": f"Invalid ({last}). Reply again with ONLY the corrected JSON rule using only the listed fields."}]
    raise RuleError(f"the AI proposal was invalid and could not be repaired: {last}")


NAMING_SYSTEM = """A statistical rule-mining pass over confirmed fraud outcomes found a detection rule for LTC
insurance claims. You are NOT inventing or changing the rule -- it is already fixed and already validated.
Your only job is to give it a clear name (3-80 chars) and a one-sentence description a fraud investigator
would understand, based on the condition and statistics you are given.
Reply with ONLY this JSON: {"name": "...", "description": "..."}"""


async def name_mined_rule(logic, stats, case_id):
    """Cosmetic LLM polish for a rule app.rule_mining already found and saved. Cannot alter the logic:
    the caller only reads "name"/"description" off the reply, everything else is ignored even if present."""
    from ..customrules import describe
    cond = describe(logic)
    facts = (f"Condition: {cond}\n"
             f"Source case: {case_id}\n"
             f"Fires on {stats.get('k_fraud')}/{stats.get('n_fraud')} confirmed-fraud case(s) and "
             f"{stats.get('background_rate', 0):.0%} of the rest of the queue (lift {stats.get('lift')}x, "
             f"n={stats.get('background_n')}).")
    msgs = [{"role": "system", "content": NAMING_SYSTEM}, {"role": "user", "content": facts}]
    msg, model = await client.chat(msgs, tier="fast", max_tokens=250, temperature=0.2)
    raw = client.extract_json(msg.get("content"))
    name = (raw.get("name") or "").strip()[:80]
    desc = (raw.get("description") or "").strip()[:400]
    if len(name) < 3 or len(desc) < 5:
        raise RuleError("empty naming reply")
    return {"name": name, "description": desc, "model": model}
