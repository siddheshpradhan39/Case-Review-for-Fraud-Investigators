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
