# AI Lead Responder

Replies to every new lead within seconds, 24/7. A FastAPI webhook receives a lead from any form, uses an LLM to score it (0–100) and classify intent, drafts a personal reply, sends it, logs everything to a database and pings the team on Slack or Discord when a hot lead arrives.

## Flow
1. A form submission hits `POST /webhook/lead` (protected by a shared secret).
2. The lead is saved immediately, so nothing is lost if a downstream service fails.
3. In the background the LLM returns `{score, intent, reply}` as structured JSON.
4. Spam is dropped; everyone else gets the drafted reply through your email relay.
5. Leads scoring 70+ fire an instant alert to your team channel.
6. `GET /leads` shows the full log with status: queued, replied, ignored_spam or failed.

With no `OPENAI_API_KEY` set it falls back to a keyword heuristic, so you can demo the whole pipeline for free.

## Run it
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
curl -X POST localhost:8000/webhook/lead -H 'content-type: application/json' \
  -d '{"name":"Ada Obi","email":"ada@example.com","message":"What is your price? I want to start ASAP."}'
curl localhost:8000/leads
```

With Docker:
```bash
docker compose up --build
```
Stop it with `docker compose down` when you're done.

## Configure
| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Enables LLM scoring and replies (model set by `LLM_MODEL`) |
| `BUSINESS_DESCRIPTION` | Tells the model what the business sells |
| `WEBHOOK_SECRET` | Required `x-webhook-secret` header value |
| `EMAIL_WEBHOOK_URL` | Relay that sends the reply email |
| `ALERT_WEBHOOK_URL` | Slack or Discord webhook for hot-lead alerts |

## Tests
```bash
pip install pytest && pytest -q
```
