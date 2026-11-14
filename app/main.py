"""AI lead responder.

A webhook that takes a new lead from any form (website, Typeform, Facebook Lead Ads via
a forwarder), scores and classifies it with an LLM, drafts a personal reply, sends it,
and logs everything to a database. Hot leads trigger an instant alert to the team.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from pydantic import BaseModel, EmailStr, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("lead-responder")

DB_PATH = os.getenv("DB_PATH", "leads.db")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
BUSINESS = os.getenv("BUSINESS_DESCRIPTION", "a digital marketing agency for small businesses")
ALERT_WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "")
EMAIL_WEBHOOK_URL = os.getenv("EMAIL_WEBHOOK_URL", "")  # e.g. Resend/SendGrid relay

app = FastAPI(title="AI Lead Responder")


class Lead(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    message: str = Field(min_length=1, max_length=4000)
    source: str = "website"


class Analysis(BaseModel):
    score: int = Field(ge=0, le=100)
    intent: str
    reply: str


def init_db() -> None:
    with closing(sqlite3.connect(DB_PATH)) as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                received_at TEXT, name TEXT, email TEXT, source TEXT, message TEXT,
                score INTEGER, intent TEXT, reply TEXT, status TEXT)"""
        )
        db.commit()


init_db()

PROMPT = """You qualify inbound leads for {business}.
Return JSON only: {{"score": 0-100, "intent": one of ["ready_to_buy","exploring","support","spam"],
"reply": a warm, specific 3-5 sentence email reply that answers their message and proposes a next step}}.
Lead name: {name}
Lead message: {message}"""


def heuristic_analysis(lead: Lead) -> Analysis:
    """Fallback when no LLM key is configured, so the pipeline still works end to end."""
    text = lead.message.lower()
    hot = any(w in text for w in ("price", "quote", "budget", "start", "asap", "hire", "book"))
    first = lead.name.split()[0]
    return Analysis(
        score=80 if hot else 40,
        intent="ready_to_buy" if hot else "exploring",
        reply=(f"Hi {first},\n\nThanks for reaching out. I've read your message and "
               "I'd love to help. Could we book a quick 15-minute call this week to go "
               "over the details?\n\nBest regards"),
    )


async def analyse(lead: Lead) -> Analysis:
    if not OPENAI_API_KEY:
        return heuristic_analysis(lead)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": MODEL,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": PROMPT.format(
                    business=BUSINESS, name=lead.name, message=lead.message)}],
            },
        )
        resp.raise_for_status()
        data = json.loads(resp.json()["choices"][0]["message"]["content"])
    return Analysis(**data)


async def post_json(url: str, payload: dict) -> None:
    if not url:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(url, json=payload)


async def process(lead_id: int, lead: Lead) -> None:
    try:
        result = await analyse(lead)
        status = "replied"
        if result.intent != "spam":
            await post_json(EMAIL_WEBHOOK_URL, {"to": lead.email, "subject": "Re: your enquiry",
                                                "text": result.reply})
        else:
            status = "ignored_spam"
        if result.score >= 70:
            await post_json(ALERT_WEBHOOK_URL, {
                "text": f"HOT LEAD ({result.score}): {lead.name} <{lead.email}>: {lead.message[:200]}",
            })
    except Exception as exc:  # never lose a lead: record the failure for retry
        log.exception("processing failed")
        result, status = Analysis(score=0, intent="error", reply=str(exc)[:500]), "failed"
    with closing(sqlite3.connect(DB_PATH)) as db:
        db.execute("UPDATE leads SET score=?, intent=?, reply=?, status=? WHERE id=?",
                   (result.score, result.intent, result.reply, status, lead_id))
        db.commit()
    log.info("lead %s -> %s (%s, score %s)", lead_id, status, result.intent, result.score)


@app.post("/webhook/lead", status_code=202)
async def receive_lead(lead: Lead, background: BackgroundTasks,
                       x_webhook_secret: str = Header(default="")) -> dict:
    if WEBHOOK_SECRET and not hmac.compare_digest(x_webhook_secret, WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="bad secret")
    with closing(sqlite3.connect(DB_PATH)) as db:
        cur = db.execute(
            "INSERT INTO leads (received_at,name,email,source,message,status) VALUES (?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), lead.name, lead.email, lead.source,
             lead.message, "queued"))
        db.commit()
        lead_id = cur.lastrowid
    background.add_task(process, lead_id, lead)
    return {"id": lead_id, "status": "queued"}


@app.get("/leads")
def list_leads(limit: int = 50) -> list[dict]:
    with closing(sqlite3.connect(DB_PATH)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT * FROM leads ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


@app.get("/health")
def health() -> dict:
    return {"ok": True, "llm": bool(OPENAI_API_KEY)}
