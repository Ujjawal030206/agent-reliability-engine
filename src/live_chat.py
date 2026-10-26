"""
Live customer chat with the Resolution Agent.

A person types as the customer; the agent works the case against a fresh
sandbox, step by step. When it asks the customer something, the chat waits for
a real answer instead of a scripted one. Disruptions from the scenario bank -
stock selling out, a payment timeout that commits anyway, a policy change, a
service outage - can be switched on before the chat starts.

There is no expected outcome for a free conversation, so the verifier checks
the universal invariants only: no duplicate refund, no action without the
customer's consent or on another customer's order, every state change
verified, and no claim the backend contradicts.

Sessions live in memory, run one agent turn at a time on a worker thread, and
expire after SESSION_TTL_S of inactivity.
"""

import threading
import time
import uuid

from src import outcome_verifier, shop_env
from src.resolution_agent import ResolutionSession

SESSION_TTL_S = 30 * 60
MAX_SESSIONS = 40
MAX_ITERATIONS_PER_MESSAGE = 12
MAX_MESSAGE_CHARS = 1000

DISRUPTIONS = {
    "sell_out": {
        "label": "Stock sells out mid-case",
        "detail": "Right after the agent's first order, stock or eligibility check, every product sells out.",
        "events": [{"on": ["get_order", "check_inventory", "check_eligibility"], "nth": 1, "timing": "after",
                    "effect": {"type": "set_stock", "sku": "*", "stock": 0},
                    "note": "Every product sold out to other customers while this case was open."}],
    },
    "gateway_timeout": {
        "label": "Payment gateway times out",
        "detail": "The first refund commits, but the agent is told the gateway timed out.",
        "events": [{"on": "issue_refund", "nth": 1, "timing": "mask_response",
                    "effect": {"type": "gateway_timeout"},
                    "note": "The payment gateway committed the refund but the response timed out."}],
    },
    "policy_change": {
        "label": "Refund limit drops to INR 2000",
        "detail": "Finance lowers the agent refund limit right after the agent's first lookup.",
        "events": [{"on": ["get_order", "check_eligibility", "search_policy"], "nth": 1, "timing": "after",
                    "effect": {"type": "update_policy", "changes": {"refund_approval_threshold": 2000}},
                    "note": "Finance lowered the agent refund limit to INR 2000 while the case was open."}],
    },
    "inventory_outage": {
        "label": "Inventory service outage",
        "detail": "The first stock, eligibility or replacement call fails with HTTP 503; retrying works.",
        "events": [{"on": ["check_inventory", "check_eligibility", "create_replacement"], "nth": 1,
                    "timing": "instead",
                    "effect": {"type": "service_unavailable",
                               "detail": "Inventory service timed out (HTTP 503). Nothing was changed; safe to retry."},
                    "note": "The inventory service was down for one call."}],
    },
}


def customers() -> list:
    """The sandbox's customers and their orders, for picking who to chat as."""
    env = shop_env.ShopEnv()
    try:
        state = env.state()
        names = {cid: (name, email) for cid, name, email in shop_env.CUSTOMERS}
        products = {sku: name for sku, name, _, _ in shop_env.PRODUCTS}
        out = []
        for cid, (name, email) in names.items():
            orders = [{
                "id": o["id"], "status": o["status"], "delivered_on": o["delivered_on"],
                "amount_paid": o["amount_paid"], "days_since_delivery": o["days_since_delivery"],
                "items": [f"{products[i['sku']]} ({i['sku']}) x{i['qty']}" for i in o["items"]],
            } for o in state["orders"].values() if o["customer_id"] == cid]
            out.append({"id": cid, "name": name, "email": email, "orders": orders})
        return out
    finally:
        env.close()


class ChatBusy(Exception):
    pass


class LiveChat:
    def __init__(self, client, customer_id: str, disruptions: list, agent_version: str, model: str = None):
        events = [dict(ev) for key in disruptions for ev in DISRUPTIONS[key]["events"]]
        self.id = uuid.uuid4().hex[:12]
        self.customer_id = customer_id
        self.disruptions = list(disruptions)
        self.env = shop_env.ShopEnv({"customer_id": customer_id, "events": events, "live": True})
        self.client = client
        self.agent_version = agent_version
        self.model = model
        self.session = None
        self.transcript = []
        self.status = "waiting_for_customer"
        self.error = None
        self.verification = None
        self.trace = None
        self.touched = time.time()
        self._lock = threading.Lock()

    # ----------------------------------------------------------------- input

    def send(self, text: str):
        text = (text or "").strip()[:MAX_MESSAGE_CHARS]
        if not text:
            raise ValueError("Type a message first.")
        with self._lock:
            if self.status == "working":
                raise ChatBusy("The agent is still working on your last message.")
            self.touched = time.time()
            before = set(self.env.consents)
            answers_question = bool(self.session and self.session.pending)
            self.env.customer_wrote(text)
            if self.session is None:
                self.session = ResolutionSession(self.client, self.env, text, self.agent_version, self.model,
                                                 pause_for_customer=True)
            else:
                self.session.answer(text)
            self.transcript.append({"role": "customer", "text": text, "answers_question": answers_question,
                                    "consents": sorted(set(self.env.consents) - before)})
            self.status, self.error = "working", None
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        try:
            stopped = self.session.advance(MAX_ITERATIONS_PER_MESSAGE)
        except Exception as exc:  # provider errors included: report them, keep the chat usable
            message = getattr(exc, "message", None) or str(exc)
            with self._lock:
                self.status, self.error = "error", message
                self.transcript.append({"role": "system", "text": f"The agent could not respond: {message}"})
            return
        with self._lock:
            if stopped == "waiting_for_customer":
                self.transcript.append({"role": "agent", "kind": "question", "text": self.session.question,
                                        "question_type": self.session.pending["tool_use"].input.get("question_type")})
            elif stopped == "completed":
                self.transcript.append({"role": "agent", "kind": "reply", "text": self.session.final_text})
            else:
                self.transcript.append({"role": "system", "text": (
                    "The agent repeated the same tool call three times and was stopped." if stopped == "loop_detected"
                    else "The agent hit its step limit for this message and was stopped.")})
            self._verify()
            self.status = "waiting_for_customer"
            self.touched = time.time()

    def _verify(self):
        trace = self.session.trace("live_chat")
        # Check every reply the customer was shown, not just the latest one.
        trace["final_text"] = "\n".join(m["text"] for m in self.transcript if m["role"] == "agent" and m["text"])
        if trace["stop_reason"] == "waiting_for_customer":
            trace["stop_reason"] = "completed"
        self.trace = trace
        self.verification = outcome_verifier.verify(trace, {"expect": {}})

    # ---------------------------------------------------------------- output

    def snapshot(self) -> dict:
        session = self.session
        steps = list(session.steps) if session else []
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "agent_version": self.agent_version,
            "model": session.model if session else None,
            "disruptions": [{"key": k, "label": DISRUPTIONS[k]["label"]} for k in self.disruptions],
            "status": self.status,
            "error": self.error,
            "transcript": list(self.transcript),
            "steps": steps,
            "decisions": [{"iteration": t["iteration"], "text": t["text"], "source": t["decision_source"]}
                          for t in (session.turns if session else [])],
            "consents": sorted(self.env.consents),
            "llm_calls": session.llm_calls if session else 0,
            "providers": list(session.providers_used) if session else [],
            "busy_s": round(session.busy_s, 1) if session else 0,
            # Read from the database only between agent turns, never while the worker is writing to it.
            "verification": self.verification if self.status != "working" else None,
            "final_state": self.trace["final_state"] if self.trace and self.status != "working" else None,
        }

    def close(self):
        with self._lock:
            self.env.close()


class ChatStore:
    def __init__(self):
        self._chats = {}
        self._lock = threading.Lock()

    def create(self, client, customer_id, disruptions, agent_version, model=None) -> LiveChat:
        if customer_id not in {cid for cid, _, _ in shop_env.CUSTOMERS}:
            raise ValueError(f"Unknown customer '{customer_id}'.")
        unknown = [d for d in disruptions if d not in DISRUPTIONS]
        if unknown:
            raise ValueError(f"Unknown disruption(s): {', '.join(unknown)}.")
        chat = LiveChat(client, customer_id, disruptions, agent_version, model)
        with self._lock:
            self._expire()
            self._chats[chat.id] = chat
        return chat

    def get(self, chat_id) -> LiveChat:
        with self._lock:
            return self._chats.get(chat_id)

    def _expire(self):
        now = time.time()
        idle = [cid for cid, c in self._chats.items() if c.status != "working" and now - c.touched > SESSION_TTL_S]
        for cid in idle:
            self._chats.pop(cid).close()
        while len(self._chats) >= MAX_SESSIONS:
            oldest = min((c for c in self._chats.values() if c.status != "working"),
                         key=lambda c: c.touched, default=None)
            if oldest is None:
                raise ChatBusy("Too many chats are running right now. Try again in a minute.")
            self._chats.pop(oldest.id).close()
