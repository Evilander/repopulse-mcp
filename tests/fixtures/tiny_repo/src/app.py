"""Small HTTP-ish application wiring."""

from src.throttle import throttle


def handle_request(user_id: str, payload: dict) -> dict:
    if not throttle(user_id):
        return {"error": "rate_limited", "user": user_id}
    return {"ok": True, "user": user_id, "echo": payload}


def healthcheck() -> dict:
    return {"status": "ok"}
