"""Weekly AI tools + high-ticket sales research → Google Doc.

Applies the "GLP-1 textbook gold mine" framework: identify markets
where (a) massive demand already exists, (b) current supply is broken
or nonexistent, and (c) a new MECHANISM solves it differently. The
mechanism doesn't have to be new in the world — just new in THAT
market. Cold outbound in SaaS = commoditized. Cold outbound in
boutique M&A / yacht brokers / private wealth = unfair advantage.

Each weekly doc evaluates this week's AI tools through that lens:
  - What does the tool do?
  - In its current market, is the mechanism commoditized?
  - Which capital-rich verticals would still find it novel?
  - For each, the 4 positioning elements (CFO vocabulary, failure
    mode architecture, whose job is on the line, financial
    consequence of inaction).

All docs are placed in a 'AI High-Ticket Sales Research' folder in
aziza.muhammadx's Drive. A static "Framework" doc lives at the top of
the folder as a reading-first reference.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from reclaim import calendar_fetch, google_docs


DEFAULT_TZ = ZoneInfo("America/Detroit")
SNAPSHOT_PATH = Path("config/research-snapshot.json")
FOLDER_NAME = "AI High-Ticket Sales Research"
FRAMEWORK_DOC_TITLE = "📕 Framework — GLP-1 Pattern (read first)"

HN_ALGOLIA = "https://hn.algolia.com/api/v1/search"

# --- Capital-rich verticals where modern AI-augmented outbound is still novel
CAPITAL_RICH_VERTICALS = [
    {
        "name": "Boutique M&A / lower-mid market PE",
        "current_motion": "Golf tournaments, senior banker relationships built over 30+ years, "
                          "personal introductions, conferences.",
        "key_pain": "Mandate sourcing is non-scalable. Each new mandate requires a senior partner's "
                    "calendar. Pipeline shrinks when seniors are tied up on existing deals.",
        "decision_maker": "Managing Director / Head of Origination",
        "financial_consequence": "$2-5M/year in deal fees missed per under-utilized senior banker. "
                                 "Bad quarter directly impacts partner distributions + LP optics.",
        "cfo_vocabulary": "deal flow, mandate pipeline, origination quotas, fee accretion, MOIC, "
                          "carry pool, LP reporting cycle",
    },
    {
        "name": "Private wealth management / RIA",
        "current_motion": "Referrals from estate attorneys, accountants, CPAs. Networking through "
                          "country clubs, charity galas. Inherited client books.",
        "key_pain": "HNWI acquisition cost is brutal. Referral network is finite. No scalable way "
                    "to identify $5M+ liquidity events (business sales, IPO lockup expirations).",
        "decision_maker": "Managing Partner / Director of Business Development",
        "financial_consequence": "$25-50K AUM-fee per HNWI client per year. One missed $10M client = "
                                 "$50K-150K annual recurring revenue at risk.",
        "cfo_vocabulary": "AUM, fee compression, organic growth rate, share-of-wallet, "
                          "household NW, AUM-per-advisor",
    },
    {
        "name": "Yacht / private aviation / luxury asset brokerage",
        "current_motion": "Boat shows, marina walk-ins, manufacturer dealer networks, "
                          "yacht clubs, captain referrals.",
        "key_pain": "Sales cycle is 6-18 months. Buyer identification is reactive (they show up). "
                    "Reseller margin is shrinking; original sales = the real cash.",
        "decision_maker": "Brokerage owner / Director of Sales",
        "financial_consequence": "$300K-2M commission per yacht. Missing 2-3 buyers a year = "
                                 "the difference between profitable and shuttered office.",
        "cfo_vocabulary": "list price, commission split, charter days, hull turnover, "
                          "season inventory, trade-up upgrade rate",
    },
    {
        "name": "Boutique law firms (M&A, T&E, securities)",
        "current_motion": "Bar association events, CLE conferences, partner-to-partner referrals, "
                          "law school alumni networks.",
        "key_pain": "New matter generation depends on partner's personal network. Lateral partner "
                    "hires bring books, leave with them. No scalable origination beyond reputation.",
        "decision_maker": "Managing Partner / Practice Group Chair",
        "financial_consequence": "$200K-500K per missed matter. Partner-hour utilization below 70% "
                                 "kills firm profitability and triggers comp committee scrutiny.",
        "cfo_vocabulary": "matter origination, billable utilization, realization rate, RPL "
                          "(revenue per lawyer), partner originations vs working, lockstep",
    },
    {
        "name": "Family offices / multi-family offices",
        "current_motion": "Referral-only. Often closed to new clients without a $50-100M minimum. "
                          "Discovery happens at private events, not in public.",
        "key_pain": "Need to find $25M+ liquidity events (business exits) BEFORE the family commits "
                    "to a competitor. The window from sale to advisor selection is 30-90 days.",
        "decision_maker": "CIO / Head of Family Office",
        "financial_consequence": "$250K-1M annual fee per family. One missed family = a decade of "
                                 "compounded fee + bespoke service revenue.",
        "cfo_vocabulary": "next-gen continuity, generational wealth transfer, alts allocation, "
                          "concentration risk, illiquidity premium, governance",
    },
    {
        "name": "Commercial real estate (specialty: industrial / data center / medical office)",
        "current_motion": "CCIM/SIOR networks, broker-of-record relationships, property tours, "
                          "ICSC conferences.",
        "key_pain": "Identifying corporate tenants in tech / healthcare BEFORE they post an RFP. "
                    "Once it's public, margin gets compressed to 1-2%.",
        "decision_maker": "Principal Broker / Tenant-Rep Director",
        "financial_consequence": "Industrial broker commission = $50K-500K per deal. Data center "
                                 "broker = $1M+. Missing 2-3 deals = a brutal year.",
        "cfo_vocabulary": "absorption rate, NNN rent, basis points, IRR, going-in cap rate, "
                          "stabilization timeline, tenant credit",
    },
]

# Multi-pass HN search — each query returns top stories, we dedupe + score
QUERIES = [
    "AI sales outbound",
    "AI lead generation",
    "AI sales agent",
    "B2B AI tool",
    "AI cold email",
    "enterprise AI sales",
    "AI SDR",
    "AI prospecting",
    "GPT sales automation",
    "AI for sales reps",
]

# Score boosters / penalties
RELEVANT_TERMS = [
    "enterprise", "b2b", "outbound", "cold email", "sales rep", "sdr", "ae",
    "revenue", "arr", "mrr", "deal", "pipeline", "agent", "automation",
    "augmented", "prospecting", "lead", "crm", "go-to-market", "gtm",
]
IRRELEVANT_TERMS = [
    "show hn", "ask hn", "image generation", "stable diffusion",
    "voice clone", "deepfake", "music generation", "tts ",
    "open source side project",
]


def _fetch_hn(query: str, since_unix: int, max_hits: int = 20) -> list[dict]:
    params = {
        "query": query,
        "tags": "story",
        "numericFilters": f"created_at_i>{since_unix}",
        "hitsPerPage": str(max_hits),
    }
    url = f"{HN_ALGOLIA}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url, headers={"User-Agent": "reclaim/0.1 (+https://github.com/azizaceo90/trackinng)"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read()).get("hits", [])


def _score(story: dict) -> int:
    base = int(story.get("points") or 0)
    text = " ".join([
        story.get("title", "") or "",
        story.get("story_text", "") or "",
    ]).lower()
    for term in RELEVANT_TERMS:
        if term in text:
            base += 5
    for term in IRRELEVANT_TERMS:
        if term in text:
            base -= 25
    return base


def gather_candidates() -> list[dict]:
    since = int((datetime.now(timezone.utc) - timedelta(days=10)).timestamp())
    seen: dict[str, dict] = {}
    for q in QUERIES:
        try:
            hits = _fetch_hn(q, since)
        except Exception as ex:
            print(f"WARN: query {q!r} failed: {ex}", file=sys.stderr)
            continue
        for h in hits:
            url_key = h.get("url") or h.get("objectID")
            if not url_key or url_key in seen:
                continue
            h["_score"] = _score(h)
            h["_matched_query"] = q
            seen[url_key] = h
    out = list(seen.values())
    out.sort(key=lambda h: -h["_score"])
    return [h for h in out if h["_score"] >= 5][:8]


def _vertical_translation_lines(vertical: dict) -> list[str]:
    return [
        f"Current motion: {vertical['current_motion']}",
        f"Key pain: {vertical['key_pain']}",
        f"Decision-maker: {vertical['decision_maker']}",
        f"Financial consequence: {vertical['financial_consequence']}",
        f"CFO vocabulary: {vertical['cfo_vocabulary']}",
    ]


def render_framework_doc_sections() -> list[dict]:
    """Static reference doc — read once."""
    s: list[dict] = []
    s.append({"kind": "heading", "text": "GLP-1 Pattern — High-Ticket Offer Framework"})
    s.append({"kind": "paragraph", "text":
        "Read this first. Don't write a single proposal without it."})

    s.append({"kind": "subheading", "text": "Why GLP-1s grew like wildfire"})
    for line in [
        "Massive demand (60% of adults overweight).",
        "No real supply (nothing else worked at scale).",
        "Novel mechanism (semaglutide cleared FDA 2017, mass-marketed 2022).",
        "Three conditions live at once. That's the textbook gold-mine offer.",
    ]:
        s.append({"kind": "bullet", "text": line})

    s.append({"kind": "subheading", "text": "Translating the pattern to B2B"})
    for line in [
        "Look for a market where massive demand already exists, current supply is broken or non-existent, "
        "and a new mechanism solves it differently than anyone else.",
        "The mechanism doesn't have to be new IN THE WORLD. Just new IN THE MARKET.",
        "Cold outbound is commoditized in SaaS (Apollo, Smartlead, everyone). It's NOVEL in boutique M&A, "
        "yacht brokerage, private wealth, family offices.",
        "Same software. Same skill. Same mechanics. Different market. Different reception entirely.",
        "That's the arbitrage of 2026.",
    ]:
        s.append({"kind": "bullet", "text": line})

    s.append({"kind": "subheading", "text": "Capital-rich verticals worth targeting"})
    for v in CAPITAL_RICH_VERTICALS:
        s.append({"kind": "paragraph", "text": f"• {v['name']}"})
        for line in _vertical_translation_lines(v):
            s.append({"kind": "bullet", "text": "    " + line})

    s.append({"kind": "subheading", "text": "Why $15K/mo operators stay stuck"})
    s.append({"kind": "paragraph", "text":
        "Technical mastery is what adjacent service providers respect. It doesn't move "
        "CFOs, operating partners, or investment committees. They only buy the elimination "
        "of a specific threat tied to capital movement."})
    s.append({"kind": "paragraph", "text":
        "Most B2B operators have confused the two their entire careers. An $80/hour consultant "
        "can know more about technical execution than the $30K/month consultant. The gap in "
        "fees isn't a gap in knowledge — it's a gap in positioning."})

    s.append({"kind": "subheading", "text": "The 4 positioning elements (the only ones that matter)"})
    for i, line in enumerate([
        "Vocabulary of their constraint — the EXACT language their CFO uses internally. "
        "Terms in their board reporting. Metrics that determine whether the next capital "
        "event succeeds or fails.",
        "Failure mode architecture — identify the operational sequence producing the failure "
        "and why it hasn't been resolved. Don't fixate on surface pain.",
        "Which decision-maker loses their job — corporate buyers are humans protecting "
        "careers, bonuses, and political capital. Figure out whose ass is on the line.",
        "Financial consequence of inaction — what does another quarter of this problem cost "
        "in dollar terms? How does that number interact with their debt facility, "
        "acquisition cycle, or investor reporting?",
    ], 1):
        s.append({"kind": "bullet", "text": f"#{i}: {line}"})

    s.append({"kind": "subheading", "text": "How fast you can build all four"})
    for line in [
        "72 hours, even in a vertical you've never sold in. McKinsey associates rotate "
        "industries every project — they don't anchor on technical knowledge, they anchor "
        "on translating any problem into risk-mitigation + capital-constraint + "
        "consequence-chain language.",
        "Three years mastering a platform = compounding in a commodity.",
        "72 hours learning how a Series B SaaS company's churn rate affects its ability to "
        "renegotiate its credit facility = enter a room where almost no one speaks the same language.",
    ]:
        s.append({"kind": "bullet", "text": line})

    s.append({"kind": "subheading", "text": "The weekly drill (apply this to each candidate)"})
    for line in [
        "What does the tool do (mechanism)?",
        "In its current market, how commoditized is the mechanism?",
        "Which capital-rich vertical above would still find this mechanism novel?",
        "Pick ONE vertical and write all 4 positioning elements for the offer.",
        "What does the resulting offer look like — pricing, deliverables, expected fee?",
    ]:
        s.append({"kind": "bullet", "text": line})

    return s


def _load_target_companies() -> list[dict]:
    """Read config/target-companies.yaml."""
    try:
        import yaml
    except ImportError:
        return []
    p = Path("config/target-companies.yaml")
    if not p.exists():
        return []
    try:
        data = yaml.safe_load(p.read_text()) or {}
    except Exception:
        return []
    return data.get("target_companies", []) or []


def _pick_vertical_for_week(verticals: list[dict], week_of: date) -> dict | None:
    """Rotate through verticals by week-of-year. So same vertical comes back every N weeks."""
    if not verticals:
        return None
    week_num = week_of.isocalendar().week
    return verticals[week_num % len(verticals)]


def render_weekly_sections(week_of: date) -> tuple[list[dict], list[dict]]:
    """Build the weekly research doc — comprehensive coverage of every vertical."""
    verticals = _load_target_companies()

    s: list[dict] = []
    s.append({"kind": "heading",
              "text": f"Week of {week_of:%b %d, %Y} — AI + High-Ticket Sales"})
    s.append({"kind": "paragraph", "text":
        "Comprehensive coverage of all 6 capital-rich verticals. Pick ONE vertical per "
        "research session, work the 3 best-fit companies, send 3 outreaches by end of week. "
        "Rotate verticals you focus on each Monday so all 6 see attention each cycle."})

    if not verticals:
        s.append({"kind": "paragraph", "text":
            "(No verticals configured — edit config/target-companies.yaml.)"})
        return s, []

    # Quick navigation index
    s.append({"kind": "subheading", "text": "This week's universe"})
    for i, v in enumerate(verticals, 1):
        cname_count = len(v.get("companies", []))
        s.append({"kind": "bullet",
                  "text": f"{i}. {v.get('vertical', '?')} ({cname_count} target companies)"})

    s.append({"kind": "subheading", "text": "30-min session game plan"})
    for line in [
        "Skim the index above. Pick the ONE vertical you want to work this week.",
        "Open that vertical's section. Read the description + key pain.",
        "Pick 3 target companies — look up their MDs / partners / brokers on LinkedIn (60 sec each).",
        "Adapt the outreach template — replace one generic claim with a company-specific detail.",
        "Schedule 3 outreaches for tomorrow morning. Log in 'this week's sends'.",
    ]:
        s.append({"kind": "bullet", "text": line})

    # --- Each vertical gets its own block ---
    for v in verticals:
        name = v.get("vertical", "?")
        industry = v.get("industry", "")
        s.append({"kind": "heading", "text": f"{name}" + (f"  ({industry})" if industry else "")})

        # Economics summary line — net margin + deal size
        margin = v.get("typical_net_margin", "")
        deal_size = v.get("typical_deal_size", "")
        if margin or deal_size:
            s.append({"kind": "paragraph", "text":
                f"💰 Net margin: {margin or '—'}  ·  Typical deal: {deal_size or '—'}"})

        s.append({"kind": "paragraph", "text": (v.get("description", "") or "").strip()})

        companies = v.get("companies", []) or []
        s.append({"kind": "subheading", "text": f"Target companies ({len(companies)})"})
        for i, c in enumerate(companies, 1):
            cname = c.get("name", "?")
            line = f"{i}. {cname}"
            if c.get("size"):
                line += f" — {c['size']}"
            s.append({"kind": "bullet", "text": line})
            if c.get("why_fit"):
                s.append({"kind": "bullet", "text": f"    Why fit: {c['why_fit']}"})
            if c.get("decision_makers"):
                s.append({"kind": "bullet", "text": f"    Decision-makers: {c['decision_makers']}"})

        template = (v.get("outreach_template") or "").strip()
        if template:
            s.append({"kind": "subheading", "text": "Outreach template"})
            s.append({"kind": "paragraph", "text": template})

        s.append({"kind": "subheading", "text": "Sends this week"})
        for line in [
            "Company A: ___ · DM: ___ · sent: ___ · reply: ___",
            "Company B: ___ · DM: ___ · sent: ___ · reply: ___",
            "Company C: ___ · DM: ___ · sent: ___ · reply: ___",
        ]:
            s.append({"kind": "bullet", "text": line})

    s.append({"kind": "heading", "text": "End-of-session review"})
    for line in [
        "Which vertical did you actually work on? ___",
        "Best outreach hook tried: ___",
        "Replies received: ___",
        "What to try differently next week: ___",
    ]:
        s.append({"kind": "bullet", "text": line})

    return s, verticals


def _ensure_framework_doc(folder_id: str, access_token: str) -> str:
    """Create the framework reference doc once if missing, share with main account."""
    existing_id = google_docs.find_doc_in_folder(
        folder_id, FRAMEWORK_DOC_TITLE, access_token,
    )
    if existing_id:
        # Make sure it's shared with the main account each run (cheap, idempotent)
        try:
            google_docs.share_with_user(
                existing_id, "muhammadaziza732@gmail.com", "writer", access_token,
            )
        except Exception:
            pass
        return f"https://docs.google.com/document/d/{existing_id}/edit"
    doc = google_docs.create_doc(FRAMEWORK_DOC_TITLE, access_token)
    google_docs.write_doc(doc["doc_id"], render_framework_doc_sections(), access_token)
    google_docs.move_to_folder(doc["doc_id"], folder_id, access_token)
    try:
        google_docs.share_with_user(
            doc["doc_id"], "muhammadaziza732@gmail.com", "writer", access_token,
        )
    except Exception:
        pass
    return doc["url"]


def _attach_doc_to_research_event(doc_id: str, doc_url: str, doc_title: str,
                                   monday: date, access_token: str) -> str | None:
    """Find this Monday's '🤖 AI tools + high-ticket sales research' event
    instance on aziza.muhammadx's calendar and attach the new doc to it
    (plus drop the URL into the description so it's visible inline).

    Returns the event's htmlLink if successful, None if event not found.
    """
    # List Monday's events (single occurrences, so the recurring instance
    # comes back as its own event).
    start = datetime(monday.year, monday.month, monday.day, 0, 0, tzinfo=DEFAULT_TZ)
    end = start + timedelta(days=1)
    events = calendar_fetch.list_events(
        access_token,
        time_min=start.isoformat(),
        time_max=end.isoformat(),
        max_results=50,
    )
    target = None
    for e in events:
        summary = (e.get("summary") or "").lower()
        if "research" in summary and ("ai tools" in summary or "🤖" in (e.get("summary") or "")):
            target = e
            break
    if not target:
        return None

    # Merge with existing attachments (don't blow away anything already there).
    existing_attachments = target.get("attachments", []) or []
    # Drop any previous-week doc attachment if it matches our title pattern
    existing_attachments = [
        a for a in existing_attachments
        if not (a.get("title", "").startswith("Week of ")
                and a.get("mimeType") == "application/vnd.google-apps.document")
    ]
    existing_attachments.append({
        "fileUrl": doc_url,
        "fileId": doc_id,
        "title": doc_title,
        "mimeType": "application/vnd.google-apps.document",
    })

    # Also prepend the link into the description so it's visible inline
    existing_desc = (target.get("description") or "").strip()
    new_desc = (
        f"📕 This week's research doc:\n{doc_url}\n\n"
        + (existing_desc if existing_desc else "")
    )

    body = {
        "attachments": existing_attachments,
        "description": new_desc,
    }
    # supportsAttachments=true is required to add Drive-file attachments
    calendar_fetch._api(
        "PATCH",
        f"/calendars/primary/events/{target['id']}",
        access_token,
        body=body,
        params={"supportsAttachments": "true"},
    )
    return target.get("htmlLink")


def generate() -> dict:
    token = google_docs.get_access_token("personal2")

    folder = google_docs.get_or_create_folder(FOLDER_NAME, token)
    framework_url = _ensure_framework_doc(folder["folder_id"], token)

    today = datetime.now(DEFAULT_TZ).date()
    # Target the NEXT Monday (today if today IS Monday). This handles the
    # case where the workflow is push-triggered on a Sunday and we want the
    # doc to land on the upcoming research event, not last week's.
    days_until_monday = (0 - today.weekday()) % 7
    monday = today + timedelta(days=days_until_monday)
    if today.weekday() == 0:   # if today's Monday, use today
        monday = today

    sections, verticals = render_weekly_sections(monday)
    title = f"Week of {monday:%b %d, %Y} — AI + High-Ticket Sales"

    # Reuse existing doc for this week if one is already in the folder
    existing_id = google_docs.find_doc_in_folder(folder["folder_id"], title, token)
    if existing_id:
        google_docs.clear_doc(existing_id, token)
        google_docs.write_doc(existing_id, sections, token)
        doc = {
            "doc_id": existing_id,
            "title": title,
            "url": f"https://docs.google.com/document/d/{existing_id}/edit",
        }
        moved_ok = True
        move_error = None
        reused = True
    else:
        doc = google_docs.create_doc(title, token)
        google_docs.write_doc(doc["doc_id"], sections, token)
        try:
            google_docs.move_to_folder(doc["doc_id"], folder["folder_id"], token)
            moved_ok = True
            move_error = None
        except Exception as ex:
            moved_ok = False
            move_error = str(ex)
            print(f"WARN: move_to_folder failed: {ex}", file=sys.stderr)
        reused = False

    # Share the doc with muhammadaziza732 so it shows up in her main Drive too
    try:
        google_docs.share_with_user(
            doc["doc_id"], "muhammadaziza732@gmail.com", "writer", token,
        )
        shared = True
    except Exception as ex:
        shared = False
        print(f"WARN: share_with_user failed: {ex}", file=sys.stderr)

    # Attach to Monday's recurring research event
    event_link = None
    try:
        event_link = _attach_doc_to_research_event(
            doc["doc_id"], doc["url"], title, monday, token,
        )
    except Exception as ex:
        print(f"WARN: attachment to calendar event failed: {ex}", file=sys.stderr)

    result = {
        "generated_at": datetime.now(DEFAULT_TZ).isoformat(),
        "week_of": monday.isoformat(),
        "folder_url": folder["url"],
        "framework_doc_url": framework_url,
        "week_doc_title": title,
        "week_doc_url": doc["url"],
        "reused_existing_doc": reused,
        "shared_with_main_account": shared,
        "calendar_event_link": event_link,
        "moved_to_folder": moved_ok,
        "move_error": move_error,
        "verticals_covered": [v.get("vertical") for v in verticals],
        "target_company_total": sum(len(v.get("companies", [])) for v in verticals),
    }
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(result, indent=2, default=str))
    return result


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="reclaim.research")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("generate", help="Build this week's research doc.")
    sub.add_parser("list", help="Show the most recent snapshot.")
    args = p.parse_args(argv)
    if args.cmd == "generate":
        r = generate()
        json.dump(r, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    elif args.cmd == "list":
        if SNAPSHOT_PATH.exists():
            print(SNAPSHOT_PATH.read_text())
        else:
            print("(no snapshot yet)")


if __name__ == "__main__":
    main()
