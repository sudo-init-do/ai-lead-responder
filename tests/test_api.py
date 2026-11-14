import importlib

from fastapi.testclient import TestClient


def make_client(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import app.main as m
    importlib.reload(m)
    return TestClient(m.app)


def test_hot_lead_is_scored_and_replied(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    r = c.post("/webhook/lead", json={"name": "Ada Obi", "email": "ada@example.com",
                                      "message": "What's your price? I want to start ASAP."})
    assert r.status_code == 202
    lead = c.get("/leads").json()[0]
    assert lead["status"] == "replied"
    assert lead["intent"] == "ready_to_buy" and lead["score"] >= 70
    assert lead["reply"].startswith("Hi Ada")


def test_rejects_bad_secret(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch, WEBHOOK_SECRET="s3cret")
    r = c.post("/webhook/lead", json={"name": "A", "email": "a@example.com", "message": "hi"},
               headers={"x-webhook-secret": "wrong"})
    assert r.status_code == 401


def test_validates_payload(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    r = c.post("/webhook/lead", json={"name": "A", "email": "not-an-email", "message": "hi"})
    assert r.status_code == 422
