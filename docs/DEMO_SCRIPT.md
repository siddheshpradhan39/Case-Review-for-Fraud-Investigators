# Demo script (target 4:30, hard limit 5:00)

Spoken pace is about 150 words a minute. Each block has the **screen** action, then the **say** text.
Persona menu (top right): start as **Alex Rivera · Investigator**; switch to **Sam Okafor · Supervisor** at 3:00. The persona resets on page reload, so do not reload mid-recording.

## Before you hit record
- [ ] Hard refresh the page, browser zoom about 125%, other tabs closed, Do Not Disturb on.
- [ ] Briefing header reads "AI reviewed 47 · 0 AI-unavailable · 0 stale". If not, the OpenRouter free-model daily quota is probably spent (error `free-models-per-day`). Add $10 credit to the OpenRouter account (raises the limit to 1000 free-model requests a day) or wait for the daily reset, then click assess-all.
- [ ] Open the **Case** view for C1001 once and click "Investigate" (or equivalent) so its trace is already computed.
- [ ] Have `LTC-2034786` ready to paste for the Blocklist step.
- [ ] Pick two low-score cases you are happy to clear, and one mid case to escalate (C1011 works well).
- [ ] Do a dry run. Free models take about 20 s per call, so never wait on a live AI call on camera except the note step, which is 2 calls.

---

## 0:00 – 0:30 · Morning briefing
**Screen:** Briefing tab. Slowly point at the four risk tiles, then Headlines, then Rule clusters.

**Say:**
"This is the Junior AI Investigator. A fraud investigator opens it in the morning and, instead of a blank queue, sees a triaged one. Of the open cases, these are critical, these high, and most are low, with the dollar exposure on each. The headlines are computed from the data, not written by a model, so they cannot be invented. Here are clusters of cases firing the same rules, and abnormal firing: rule R05 fires on 7 of 11 Home Health Aide cases, far above the rest."

## 0:30 – 1:15 · A critical case
**Screen:** click the top case (C1031) from "Top cases to open first". Show the summary, the indicator list, the risk level, the recommended action. Click ✓ on one indicator and ✗ on another.

**Say:**
"Opening a case, I see the rule score and the AI score side by side. The AI gives a short summary, the indicators that drove it, a risk level and a next step, here escalate to SIU. Every indicator cites the exact field and value from the record, and the system re-checks each one against the data. I can accept a finding or reject it. A rejected finding is not re-asserted next time. The AI advises. I decide."

## 1:15 – 2:00 · C1001, the case rules alone miss
**Screen:** open C1001. Show its rules fired (few) and the linked-claim indicator. Open the Investigation trace.

**Say:**
"This is my favourite case. On its own signals C1001 looks clean. But its claim number is reused on C1031, a critical case. No single-row rule can see that, so the system links cases by claim number. In the trace you can see how it reasoned: specialists run only where their domain fired, an adversarial challenger argues the benign story, a verifier decides, and a linked-case guardrail stops the AI downgrading it below the critical case it is tied to. Every step, its tokens and its cost are recorded."

## 2:00 – 2:45 · Notes steer the AI
**Screen:** on C1011, add the note "Provider confirmed relocation, verified by phone." Show the assessment marked stale, re-assess, then ask the chat: "What do the notes say about this case?" Then delete the note.

**Say:**
"Notes are how an investigator steers the AI. I add what I learned on the phone. The assessment is now marked stale, because its inputs changed, and re-running costs only two calls, since the specialists are reused. The score moves down, never up, and stays within a fixed guardrail. The chatbot can quote the note. And if I delete it, it disappears from the assessment, the chat and related-case context. Notes are treated as untrusted text, so an injected instruction inside one is not obeyed. I tested that."

## 2:45 – 3:30 · Bulk clear and escalation
**Screen:** Queue tab. Filter score below 10 and show the guardrail preview (what is blocked and why). Then select two cases and click **Clear selected**, entering a reason. Then escalate C1011 with a reason. Switch persona to **Sam Okafor · Supervisor**, open the Supervisor tab, and approve.

**Say:**
"For the benign majority, bulk clear works by filter, for example score below 10, or by hand-picked selection. The preview shows anything the guardrails block, such as a case with a high rule or a linked critical case, and why. Every clear needs a reason and is audited. To escalate, I write a reason and the AI drafts the handoff. Now I am the supervisor, and I approve or return it. The AI never changes a case status by itself."

## 3:30 – 4:15 · Blocklist and Rules
**Screen:** Blocklist tab. New entry: claim number `LTC-2034786`. Show the preview (C1001 and C1031), save as supervisor, show the DECLINED cases, then override one with a reason. Then Rules tab: open a rule, show the backtest.

**Say:**
"Some decisions should not depend on a model. The blocklist is a set of hard rules: claim numbers, providers, segments, conditions, or fraud history. A match auto-declines the case. Investigators can only propose entries. A supervisor enacts them. I preview the impact first, and anything covering more than a quarter of the queue needs explicit confirmation. A supervisor can override a decline with a written reason, which becomes a note. In the Rules tab, I can create or tune a rule and see a live backtest of precision and recall. With no fraud labels in the data, those backtests use synthetic labels and say so."

## 4:15 – 4:30 · Wrap
**Screen:** back to the Briefing.

**Say:**
"What is not built: auth, a provider graph, and a false-negative safety net, which is my first next step. Some cases can show 'AI unavailable' when the free models rate-limit. The system shows that honestly rather than inventing an answer. Thanks for watching."

---

## If something goes wrong on camera
- **AI call is slow:** keep talking about what the guardrails do; do not cut. Or pause recording and trim later.
- **A case shows "AI unavailable":** say "the free model rate-limited; the system shows it rather than inventing output", then move on.
- **Persona reset after reload:** re-select the persona from the top-right menu.
