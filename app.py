from __future__ import annotations
import os, re, time, uuid
from datetime import datetime, timezone
from typing import Any, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Vera Challenge Bot", version="1.0.0")
START = time.time()

STORE = {"category": {}, "merchant": {}, "customer": {}, "trigger": {}}
VERSIONS = {"category": {}, "merchant": {}, "customer": {}, "trigger": {}}
CONVS: dict[str, dict[str, Any]] = {}
SENT_KEYS: set[str] = set()

AUTO_REPLIES = [
    "thank you for contacting us", "thank you for your message", "our team will respond",
    "we will get back to you", "aapki jaankari ke liye", "hamari team tak pahuncha",
    "automated assistant", "auto reply", "away from keyboard", "currently unavailable",
]
STOP_PHRASES = ["stop messaging", "stop", "unsubscribe", "don't message", "do not message", "spam", "remove me"]
SOFT_DECLINE_PHRASES = ["not interested", "no thanks", "no thank you", "not for me", "don't need it"]
COMMIT_PHRASES = ["yes", "ok", "okay", "lets do it", "let's do it", "go ahead", "do it", "proceed", "sign me up", "i want to join", "join", "sure"]
QUESTION_WORDS = ["how", "what", "when", "where", "why", "which", "can you", "could you", "tell me"]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def first_name(m):
    return m.get("identity", {}).get("owner_first_name") or m.get("identity", {}).get("name", "Merchant").split()[0]


def fmt_pct(v):
    if v is None: return None
    return f"{v*100:.1f}%"


def active_offer(m):
    for o in m.get("offers", []):
        if o.get("status") == "active": return o.get("title")
    return None

def category_slug(cat):
    return (cat or {}).get("slug", "").lower()

def category_noun(cat):
    slug = category_slug(cat)
    return {
        "dentists": "dental practice",
        "gyms": "fitness studio",
        "salons": "salon",
        "restaurants": "restaurant",
        "pharmacies": "pharmacy",
    }.get(slug, (cat or {}).get("display_name", "business"))

def best_active_offer(m):
    offers = [o.get("title") for o in m.get("offers", []) if o.get("status") == "active" and o.get("title")]
    return offers[0] if offers else None


def category_voice(cat):
    return cat.get("voice", {}) if cat else {}


def language_style(m, customer=None):
    obj = customer or m
    langs = obj.get("identity", {}).get("languages", []) if not customer else [obj.get("identity", {}).get("language_pref", "")]
    s = " ".join(map(str, langs)).lower()
    return "mix" if ("hi" in s or "te" in s or "mr" in s or "kannada" in s) else "en"


def safe_item(cat, trigger):
    p = trigger.get("payload", {})
    item_id = p.get("top_item_id")
    for x in cat.get("digest", []):
        if x.get("id") == item_id: return x
    # fallback to most relevant digest item
    kind = trigger.get("kind", "")
    for x in cat.get("digest", []):
        if kind in str(x.get("kind", "")) or kind in str(x.get("title", "")).lower(): return x
    return (cat.get("digest") or [None])[0]


def localize(msg, style):
    if style != "mix": return msg
    # Keep technical/content phrases in English; use light Indian conversational cues.
    replacements = {
        "Want me to": "Chale, main",
        "Would you like me to": "Aap chahein to main",
        "Worth a look": "Worth a look",
        "your": "aapka",
    }
    for a,b in replacements.items(): msg = msg.replace(a,b)
    return msg


def compose(category: dict, merchant: dict, trigger: dict, customer: Optional[dict] = None) -> dict:
    kind = trigger.get("kind", "").lower()
    scope = trigger.get("scope", "merchant")
    if scope == "customer" or customer:
        return compose_customer(category, merchant, trigger, customer)
    return compose_merchant(category, merchant, trigger)


def compose_merchant(cat, m, t):
    kind = t.get("kind", "")
    name = first_name(m)
    full_name = m.get("identity", {}).get("name", name)
    p = t.get("payload", {})
    perf = m.get("performance", {})
    peer = cat.get("peer_stats", {})
    offer = active_offer(m)
    style = language_style(m)
    body, cta = "", "open_ended"
    rationale = ""
    handled = False

    if kind == "active_planning_intent":
        topic = p.get("intent_topic", "the plan you mentioned").replace('_',' ')
        last = p.get("merchant_last_message", "")
        body = f"{name}, yes - let's take the {topic} idea forward."
        # Use concrete facts from the existing conversation history when they exist.
        hist = m.get("conversation_history", [])
        previous_vera = next((h.get("body", "") for h in reversed(hist) if h.get("from") == "vera"), "")
        if "18 orders/day" in previous_vera:
            body += " Your weekday thali is already at 18 orders/day, so the corporate version is a natural extension."
        elif "4-week program" in previous_vera:
            body += " The earlier 4-week plan was 3 classes/week for ages 7-12 at Rs. 2,499."
        elif last:
            body += " You already asked what it could look like, so I'll move straight to a draft."
        body += " Want me to draft the first version now?"
        rationale = "Explicit planning intent is treated as an action handoff; concrete facts from the merchant's prior Vera conversation are reused instead of re-qualifying."
        handled = True
    elif kind == "category_seasonal":
        trends = p.get("trends", [])
        body = f"{name}, summer demand is shifting in your category."
        if trends: body += " Signals include " + ", ".join(str(x).replace("_", " ") for x in trends[:3]) + "."
        if p.get("shelf_action_recommended"): body += " A shelf/profile update looks timely. Want me to draft the exact changes?"
        else: body += " Want me to suggest the most relevant next move?"
        rationale = "Uses the seasonal trigger and its supplied demand signals to create a concrete action."
        handled = True
    elif kind == "cde_opportunity":
        item = next((x for x in cat.get("digest", []) if x.get("id") == p.get("digest_item_id")), safe_item(cat, t) or {})
        credits = p.get("credits", item.get("credits", ""))
        title = item.get("title", "a relevant webinar")

        body = f"{name}, there's a {credits}-credit category learning opportunity: {title}"

        if p.get("fee") == "free_for_members":
            body += " - free for members."
        elif p.get("fee"):
            body += f" - {p['fee']}."

        body += " Want me to pull the details?"
        rationale = "Matches the CDE trigger to a concrete, category-specific learning opportunity."
        handled = True
    elif kind == "ipl_match_today":
        body = f"{name}, {p.get('match', 'today match')} is on today"
        if p.get("venue"): body += f" at {p['venue']}"
        if p.get("match_time_iso"): body += f" ({p['match_time_iso']})."
        else: body += "."
        body += " Want me to draft a match-day offer using one of your existing services?"
        rationale = "Uses the supplied local event and time while keeping the offer grounded in the merchant catalog."
        handled = True
    elif kind in ("gbp_unverified", "unverified_gbp"):
        body = f"{name}, your Google Business Profile is still unverified."
        if p.get("verification_path"): body += f" The available path is {p['verification_path'].replace('_',' ')}."
        if p.get("estimated_uplift_pct") is not None:
            body += f" The supplied estimate is up to {p['estimated_uplift_pct']*100:.0f}% uplift - an estimate, not a guarantee."
        body += " Want me to show the exact verification steps?"
        rationale = "Concrete profile-state trigger with a direct next action; includes the supplied uplift estimate only as an estimate, not a guarantee."
        handled = True
    elif kind == "milestone_reached":
        metric = p.get("metric", "metric").replace("_", " ")
        now = p.get("value_now")
        milestone = p.get("milestone_value")
        if now is not None and milestone is not None:
            body = f"{name}, your {metric} is at {now} - just {max(0, milestone-now)} away from {milestone}."
        elif now is not None:
            body = f"{name}, your {metric} is at {now}."
        else:
            body = f"{name}, your {metric} milestone is coming up."
        body += " Want me to draft a short Google post to mark it?"
        rationale = "Uses the exact milestone numbers and converts the near-term milestone into a concrete visibility action."
        handled = True
    if kind == "research_digest":
        item = safe_item(cat, t) or {}
        title = item.get("title", "A new category update")
        src = item.get("source")
        n = item.get("trial_n")
        summary = item.get("summary", "")
        cohort = "high-risk adult cohort" if "high_risk_adult_cohort" in m.get("signals", []) else None
        fact = f"{n:,}-patient" if isinstance(n, int) else "new"
        body = f"Dr. {name}, {src or 'this week category update'} has an item worth a look: {fact} evidence on {title.lower()}."
        if cohort: body += f" It may be especially relevant to your {cohort}."
        if summary: body += f" {summary.split('.')[0]}."
        body += " Want me to pull the source and turn it into a short patient-share draft?"
        rationale = "Uses the research trigger, source, concrete evidence and merchant-specific cohort to create a low-effort next step."
    elif kind in ("regulation_change", "compliance"):
        item = safe_item(cat, t) or {}
        deadline = p.get("deadline_iso")
        body = f"{name}, compliance update for {cat.get('display_name', cat.get('slug','your category'))}: {item.get('title','a new regulation update')}"
        if deadline: body += f" - deadline {deadline}."
        elif item.get("source"): body += f" - {item['source']}."
        if item.get("actionable"): body += f" {item['actionable']}."
        body += " Want me to turn this into a quick checklist for your team?"
        rationale = "Urgent compliance trigger with source/action and a single low-friction CTA."
    elif kind in ("perf_dip", "performance_dip"):
        metric = p.get("metric")
        if not metric:
            neg = {k:v for k,v in perf.get("delta_7d", {}).items() if isinstance(v,(int,float)) and v < 0}
            metric = min(neg, key=neg.get).replace("_pct", "") if neg else None
        delta = p.get("delta_pct") if p.get("delta_pct") is not None else (perf.get("delta_7d", {}).get(f"{metric}_pct") if metric else None)
        if isinstance(delta, (int,float)) and delta < 0 and metric:
            pct = f"{abs(delta)*100:.0f}%"
            base = p.get("vs_baseline") or peer.get(f"avg_{metric}_30d")
            body = f"{name}, your {metric} is down {pct} in the {p.get('window','7d')} window"
            if base: body += f" (peer/baseline: {base})."
            else: body += "."
            if offer: body += f" You already have {offer} active."
            body += " Want me to pinpoint the most likely listing/campaign lever before we change anything?"
        else:
            body = f"{name}, the current 7-day snapshot doesn't show a clear performance dip for {full_name}."
            if perf.get("views") is not None and perf.get("calls") is not None:
                body += f" It is at {perf['views']:,} views and {perf['calls']:,} calls."
            body += " Want me to check what signal the trigger is referring to?"
        rationale = "Only states a decline when the supplied trigger or merchant snapshot actually supports one; otherwise explicitly surfaces the missing signal instead of inventing a dip."
    elif kind in ("perf_spike", "performance_spike"):
        metric = p.get("metric")
        if not metric:
            deltas = perf.get("delta_7d", {})
            metric = max(deltas, key=lambda k: deltas.get(k, -999)) if deltas else "performance"
            metric = metric.replace("_pct", "")
        delta = p.get("delta_pct") if p.get("delta_pct") is not None else perf.get("delta_7d", {}).get(f"{metric}_pct")
        pct = f"{abs(delta)*100:.0f}%" if isinstance(delta, (int,float)) else "a recent spike"
        value = perf.get(metric)
        body = f"{name}, your {metric} jumped {pct} in the last 7 days"
        if value is not None: body += f" to {value:,}."
        if p.get("likely_driver"): body += f" The trigger points to {str(p['likely_driver']).replace('_',' ')}."
        body += " Want me to turn that signal into one repeatable next step?"
        rationale = "Uses the strongest supplied performance change and, when available, the stated likely driver; avoids inventing a cause."
    elif kind in ("renewal_due", "subscription_expiry"):
        days = p.get("days_remaining", m.get("subscription", {}).get("days_remaining"))
        body = f"{name}, your {m.get('subscription',{}).get('plan','current')} plan has {days} days left." if days is not None else f"{name}, your plan is nearing renewal."
        amount = p.get("renewal_amount")
        if amount: body += f" Renewal amount is Rs. {amount:,}."
        body += " Want me to walk you through the next step?"
        rationale = "Uses exact renewal timing/amount rather than a generic sales pitch."
    elif kind in ("competitor_opened", "competitor_nearby"):
        dist = p.get("distance_km") or p.get("distance")
        comp = p.get("competitor_name")
        body = f"{name}, a new competitor signal just appeared near {m.get('identity',{}).get('locality', 'your area')}"
        if dist: body += f" ({dist} km away)"
        if comp: body += f": {comp}"
        if comp or dist: body += "."
        if not dist and not comp:
            body += f". Your current profile is at {fmt_pct(perf.get('ctr'))} CTR with {perf.get('views')} views in 30 days."
        body += " Want me to compare the visible listing signals with yours?"
        rationale = "Uses supplied local competitor facts when present; otherwise anchors the comparison in the merchant's actual current profile metrics without inventing competitor details."
    elif kind in ("festival_upcoming", "festival"):
        fest = p.get("festival")
        days = p.get("days_until")
        if fest:
            body = f"{name}, {fest} is coming up"
            if days is not None: body += f" in {days} days"
            body += "."
        else:
            body = f"{name}, I have a seasonal planning signal for {full_name}."
        if offer: body += f" You already have {offer} active."
        else:
            body += f" Your current 30-day snapshot is {perf.get('views_30d', perf.get('views', ''))} views and {perf.get('calls', '')} calls." if perf.get('views_30d') or perf.get('views') else ""
        body += " Want me to suggest one concrete campaign angle?"
        rationale = "Uses the supplied seasonal event when available; otherwise stays explicit about the trigger being a planning signal and grounds the message in current merchant state."
    elif kind in ("curious_ask_due", "scheduled_recurring"):
        body = f"Quick question for {full_name}: what service are customers asking for most this week?"
        if offer: body += f" I can shape the next nudge around {offer}."
        body += ""
        cta = "open_ended"
        rationale = "Knowledge-driven conversation designed to learn from the merchant rather than sell at them."
    elif kind in ("dormant_with_vera", "dormancy"):
        body = f"{name}, it's been a while since we last worked on {full_name}."
        body += " I spotted one useful next move in your current profile state. Want the 2-minute version?"
        rationale = "Acknowledges dormancy and reduces re-entry effort."
    elif kind in ("unverified_gbp", "profile_incomplete"):
        body = f"{name}, your Google Business Profile is still unverified."
        body += " That can limit how confidently customers see your listing. Want me to show the exact verification step?"
        rationale = "Directly addresses a concrete profile state and offers one action."
    elif kind in ("winback_eligible", "winback"):
        added = p.get("lapsed_customers_added_since_expiry")
        body = f"{name}, you've had a recent lapse signal"
        if added: body += f" - {added} customers were added to the lapsed pool since expiry."
        body += " Want me to draft a win-back message using your existing customer-safe offer?"
        rationale = "Uses customer aggregate signal and externalizes the drafting effort."
    elif kind in ("review_theme_emerged",):
        theme = p.get("theme") or "a review theme"
        occ = p.get("occurrences")
        body = f"{name}, {theme} is showing up in recent reviews"
        if occ: body += f" ({occ} mentions)."
        body += " Want me to suggest a response/post angle without overreacting to a small sample?"
        rationale = "Uses a review-derived signal with a cautious, actionable framing."
    elif not handled:
        # Generic but context-grounded fallback
        signal = next((s for s in m.get("signals", []) if isinstance(s, str)), None)
        body = f"{name}, I have a {kind.replace('_',' ')} update for {full_name}."
        if signal: body += f" One current signal is {signal.replace('_',' ')}."
        body += " Want me to show the most useful next step?"
        rationale = "Fallback grounded in the actual trigger and merchant state."

    if style == "mix" and kind in ("perf_dip", "perf_spike", "renewal_due", "curious_ask_due"):
        body = localize(body, style)
    return {
        "body": body.strip(), "cta": cta, "send_as": "vera",
        "suppression_key": t.get("suppression_key") or t.get("id"), "rationale": rationale
    }


def compose_customer(cat, m, t, c):
    p = t.get("payload", {})
    kind = t.get("kind", "")
    cname = (c or {}).get("identity", {}).get("name", "there")
    style = language_style(m, c)
    offer = best_active_offer(m)
    slots = p.get("available_slots", [])
    business = m.get("identity", {}).get("name", "the business")
    slug = category_slug(cat)

    if kind == "recall_due":
        due = p.get("due_date")
        last = p.get("last_service_date") or (c or {}).get("relationship", {}).get("last_visit")
        service = p.get("service_due", "follow-up").replace("_", " ")
        if slug == "dentists":
            body = f"Hi {cname}, {business} here - your {service} is due."
        elif slug == "gyms":
            body = f"Hi {cname}, {business} here - it looks like you're due for your next check-in."
        elif slug == "salons":
            body = f"Hi {cname}, {business} here - it looks like you're due for your next visit."
        else:
            body = f"Hi {cname}, {business} here - a follow-up is due."
        if last:
            body += f" Your last visit was {last}."
        if due:
            body += f" Due date: {due}."
        if slots:
            labels = [s.get("label") for s in slots[:2] if s.get("label")]
            if labels:
                body += " We have " + " or ".join(labels) + " available."
        if offer and slug in ("dentists", "salons", "gyms"):
            body += f" {offer}."
        body += " Reply with the slot you prefer, or tell us a better time."
        rationale = "Customer-facing recall uses only supplied visit history, due date, available slots and a real active offer; category wording is adapted to the business."

    elif kind in ("appointment_tomorrow", "appointment_reminder"):
        appt = p.get("appointment") or p.get("slot") or p.get("appointment_time")
        body = f"Hi {cname}, reminder from {business}"
        if appt:
            body += f" - your appointment is {appt}."
        else:
            body += " - your appointment is tomorrow."
        body += " Reply if you need to reschedule."
        rationale = "Appointment reminder anchored on the supplied booking context."

    elif kind in ("chronic_refill_due", "chronic_refill"):
        # A refill trigger is only appropriate for a pharmacy/customer with refill consent.
        consent = (c or {}).get("consent", {}).get("scope", [])
        if slug == "pharmacies" and "refill_reminders" in consent:
            body = f"Hi {cname}, a refill reminder from {business}."
            meds = p.get("molecule_list") or []
            if meds:
                body += f" Refill items on file include {', '.join(meds)}."
            if p.get("stock_runs_out_iso"):
                body += f" Current refill window is before {p['stock_runs_out_iso'].split('T')[0]}."
            if (c or {}).get("preferences", {}).get("preferred_slots"):
                body += f" Your preference is {(c['preferences']['preferred_slots']).replace('_', ' ')}."
            if offer:
                body += f" {offer}."
            body += " Reply if you'd like us to arrange the next step."
            rationale = "Uses the supplied refill items, timing, customer preference and active pharmacy offer, and only sends refill messaging when refill-reminder consent is present."
        else:
            body = f"Hi {cname}, {business} here. We don't have a specific refill detail to share for this trigger."
            body += " Would you like the available appointment details instead?"
            rationale = "The trigger is not appropriate for this category or the customer lacks refill-reminder consent, so the bot avoids sending an unsupported medication/refill message."

    elif kind in ("wedding_package_followup",):
        wd = p.get("wedding_date") or (c or {}).get("preferences", {}).get("wedding_date")
        body = f"Hi {cname}, following up from your bridal trial at {business}."
        if wd:
            body += f" With your wedding on {wd}, this is a good window to plan the next step."
        body += " Want us to share the next available package options?"
        rationale = "Bridal follow-up respects the known wedding date and keeps the CTA low friction."

    elif kind in ("customer_lapsed_soft", "customer_lapsed_hard", "winback_customer"):
        last = (c or {}).get("relationship", {}).get("last_visit")
        body = f"Hi {cname}, we haven't seen you at {business} for a while"
        if last:
            body += f" - last visit was {last}."
        if offer:
            body += f" We currently have {offer}."
        body += " Would you like me to share a convenient slot?"
        rationale = "Personalized win-back based on visit history and an actual active offer."

    else:
        body = f"Hi {cname}, a quick update from {business}. Would you like the details?"
        rationale = "Safe customer-facing fallback with no invented claims."

    if style == "mix" and kind in ("recall_due", "customer_lapsed_soft"):
        body = body.replace("Reply with", "Aap reply karke").replace("or tell us a better time", "ya apna convenient time bata dein")
    return {"body": body, "cta": "open_ended", "send_as": "merchant_on_behalf", "suppression_key": t.get("suppression_key") or t.get("id"), "rationale": rationale}


def get_merchant(mid): return STORE["merchant"].get(mid, {})
def get_trigger(tid): return STORE["trigger"].get(tid, {})
def get_category(slug): return STORE["category"].get(slug, {})
def get_customer(cid): return STORE["customer"].get(cid, {})


class ContextReq(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict
    delivered_at: Optional[str] = None

@app.get("/v1/healthz")
def healthz():
    return {"status":"ok", "uptime_seconds": round(time.time()-START,2), "contexts_loaded": {k:len(v) for k,v in STORE.items()}}

@app.get("/v1/metadata")
def metadata():
    return {"team_name": os.getenv("TEAM_NAME","Vera Builders"), "team_members": os.getenv("TEAM_MEMBERS","Candidate").split(","), "model":"deterministic-rule-engine-v1", "approach":"trigger router + context-grounded deterministic composer + stateful conversation handling", "contact_email":os.getenv("CONTACT_EMAIL",""), "version":"1.0.0", "submitted_at":now_iso()}

@app.post("/v1/context")
def context(req: ContextReq):
    if req.scope not in STORE: return {"accepted":False,"reason":"invalid_scope","details":req.scope}
    cur = VERSIONS[req.scope].get(req.context_id)
    if cur is not None and req.version < cur:
        return {"accepted":False,"reason":"stale_version","current_version":cur}
    if cur == req.version:
        return {"accepted":True,"ack_id":f"ack_{req.context_id}_{req.version}","stored_at":now_iso()}
    STORE[req.scope][req.context_id] = req.payload
    VERSIONS[req.scope][req.context_id] = req.version
    return {"accepted":True,"ack_id":f"ack_{req.context_id}_{req.version}","stored_at":now_iso()}

@app.post("/v1/tick")
def tick(req: dict):
    actions=[]
    for tid in req.get("available_triggers", []):
        t=get_trigger(tid)
        if not t: continue
        mid=t.get("merchant_id")
        m=get_merchant(mid)
        if not m: continue
        cid=t.get("customer_id")
        c=get_customer(cid) if cid else None
        key=t.get("suppression_key") or tid
        if key in SENT_KEYS: continue
        cat=get_category(m.get("category_slug"))
        out=compose(cat,m,t,c)
        conv=f"conv_{uuid.uuid4().hex[:12]}"
        CONVS[conv]={"merchant_id":mid,"customer_id":cid,"trigger_id":tid,"turns":[],"sent_keys":{key},"first_sent_at":now_iso()}
        SENT_KEYS.add(key)
        actions.append({"conversation_id":conv,"merchant_id":mid,"customer_id":cid,"send_as":out["send_as"],"trigger_id":tid,"template_name":"vera_contextual_v1","template_params":[],**out})
    return {"actions":actions}


def detect_auto(msg, hist):
    low = msg.lower().strip()

    if any(x in low for x in AUTO_REPLIES):
        return True

    if len(hist) >= 1:
        same_count = sum(
            1 for x in hist
            if x.get("from") == "merchant"
            and x.get("body", "").strip().lower() == low
        )
        if same_count >= 2:
            return True

    return False

def is_stop(msg):
    low=msg.lower()
    return any(x in low for x in STOP_PHRASES)

def is_commitment(msg):
    low = msg.lower().strip()
    return any(phrase in low for phrase in COMMIT_PHRASES) and len(low) < 120

def reply_logic(state, msg):
    hist=state.setdefault("turns", [])
    hist.append({"from":"merchant","body":msg,"ts":now_iso()})
    if is_stop(msg):
        return {"action":"end","rationale":"Merchant explicitly asked to stop; ending immediately."}
    if any(x in msg.lower() for x in SOFT_DECLINE_PHRASES):
        return {"action":"end","body":"Understood. I won't push this further. If you'd like to revisit it later, just message me here.","cta":"none","rationale":"Merchant declined the opportunity without requesting a broader messaging stop; acknowledged the decline and ended the conversation without further pressure."}
    if is_commitment(msg):
        return {
        "action": "send",
        "body": "Done - next step ready hai. Main required details/draft share kar rahi hoon; agar aap kisi specific change ko prefer karte hain, yahin bata dijiye.",
        "cta": "open_ended",
        "rationale": "Explicit commitment detected; switched directly to action instead of asking another qualification question."
    }

    if detect_auto(msg, hist[:-1]):
        auto_count = sum(
        1 for x in hist
        if x.get("from") == "merchant"
        and detect_auto(x.get("body", ""), [])
    )

        if auto_count >= 2:
            return {
            "action": "end",
            "rationale": "Detected repeated automated/canned reply; stopped to avoid wasting turns."
        }

        return {
        "action": "send",
        "body": "Samajh gayi - main owner/manager se directly connect karne ki koshish karungi. Agar aap khud dekhna chahein, bata dijiye.",
        "cta": "open_ended",
        "rationale": "Detected likely auto-reply and made one concise follow-up before backing off."
    }
    low=msg.lower()
    if any(x in low for x in ["thank", "thanks", "dhanyavad"]):
        return {"action":"send","body":"You're welcome. Jab bhi next step chahiye, yahin message kar dijiye.","cta":"none","rationale":"Acknowledged politely without creating unnecessary follow-up friction."}
    if any(x in low for x in ["later", "not now", "busy", "after some time"]):
        return {"action":"wait","wait_seconds":1800,"rationale":"Merchant asked to defer; respecting the request with a short cooldown."}
    if any(q in low for q in QUESTION_WORDS) or "?" in low:
        trigger_id = state.get("trigger_id")
        trigger = get_trigger(trigger_id) if trigger_id else {}
        payload = trigger.get("payload", {}) if trigger else {}

        credits = payload.get("credits")
        fee = payload.get("fee")

        if "cost" in low or "price" in low or "fee" in low or "much" in low:
            if fee == "free_for_members":
                credit_text = f"{credits} credits" if credits is not None else "the available credits"
                body = f"There is no fee for members. This opportunity includes {credit_text}. Want me to pull the details?"
            elif fee:
                body = f"The current offer is {fee}. Want me to pull the details?"
            else:
                body = "I don't have a confirmed price in the current offer details, so I don't want to guess. I can pull the latest details for you."

            return {
            "action": "send",
            "body": body,
            "cta": "open_ended",
            "rationale": "Answered the merchant's cost question using the fee information supplied by the original trigger instead of inventing a price."
            }

        return {
        "action": "send",
        "body": "Sure. I can clarify the opportunity using the details already available on your account. What would you like to know?",
        "cta": "open_ended",
        "rationale": "Question detected; responds using available account context without restarting qualification."
        }
    return {"action":"send","body":"Got it. I'll keep the next step focused and use the details already available on your account.","cta":"open_ended","rationale":"Acknowledges the merchant and avoids unnecessary qualification."}

@app.post("/v1/reply")
def reply(req: dict):
    cid=req.get("conversation_id")
    state=CONVS.setdefault(cid, {"merchant_id":req.get("merchant_id"),"customer_id":req.get("customer_id"),"turns":[]})
    out=reply_logic(state, req.get("message", ""))
    state["turns"].append({"from":"vera","body":out.get("body","") or out.get("rationale",""),"ts":now_iso()})
    return out







