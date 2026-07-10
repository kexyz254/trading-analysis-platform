"""AlertManager: a single, auditable feed for the handful of conditions
serious enough to need a human's attention, distinct from the ordinary
INFO-level noise of a 24/7 cycle loop (dozens of symbols, every 5 minutes).
Before this, a circuit breaker trip or a significant feature-drift warning
was just one more line in a log file nobody is realistically tailing in
real time - genuinely serious events had no louder signal than routine
"no_action" cycle logs.

Every alert is persisted to the journal (survives restarts, queryable by
the dashboard) regardless of whether external delivery is configured.
External delivery is a separate, opt-in layer on top of that - unset by
default, so nothing calls out over the network unless an operator
deliberately configures it. Two independent channels:
  - A Slack/Discord-compatible webhook (any endpoint accepting a JSON POST
    with a "text" field), via ALERT_WEBHOOK_URL.
  - Email via Gmail SMTP (smtp.gmail.com:587, STARTTLS, an app password -
    Gmail rejects a plain account password for SMTP auth once 2FA is on),
    via SMTP_USERNAME/SMTP_PASSWORD/ALERT_EMAIL_TO.
Both are best-effort: a delivery failure on either is logged and swallowed,
never allowed to interrupt the trading loop that raised the alert - the
journal record above already happened regardless.
"""

from __future__ import annotations

import html as _html
import logging
import smtplib
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any, Dict, List, Optional

import requests

from trading_assistant.persistence.journal import Journal

logger = logging.getLogger(__name__)

# Ordered least -> most severe.
SEVERITIES = ("info", "warning", "critical")

# Structured, plain-language teaching content per alert category, for the
# email card - written for someone with zero trading background, since
# email is the one channel most likely to reach a person directly rather
# than someone already looking at the dashboard. Broken into 4 fixed
# questions (what happened / why it matters / what the system did / do you
# need to act) rather than one paragraph, so it reads as a structured
# explanation, not a wall of text - a v1 single-paragraph version was tried
# and explicitly rejected as not educational enough. The raw `alert.message`
# (already fairly specific, e.g. "BTC/USDT: ...") is still shown separately
# below all of this, so nothing is hidden - this is teaching context ON TOP
# of the exact technical facts, never a replacement for them. New alert
# categories fall back to _DEFAULT_CATEGORY_INFO rather than needing an
# entry here first.
_CATEGORY_INFO: Dict[str, Dict[str, Any]] = {
    "position_unprotected": {
        "icon": "\U0001F513",
        "title": "A Real Position Has No Safety Net Right Now",
        "what_happened": "A coin the system genuinely holds - real money, real exchange balance - just "
        "lost its protective stop-loss order, or one couldn't be placed for it, and the retry also failed.",
        "why_it_matters": "Without a resting stop-loss order sitting on the exchange, nothing will "
        "automatically sell this position if the price drops sharply - it's currently exposed to the full "
        "move, with no safety net, until this is fixed.",
        "system_action": "Already tried to place or replace the protective stop and it didn't go through "
        "(commonly a brief exchange connection issue). It will keep retrying on the next cycle.",
        "needs_action": True,
        "action_detail": "Yes - if this keeps repeating for the same coin, check your exchange account "
        "directly and consider manually placing a stop-loss or closing the position yourself.",
    },
    "circuit_breaker": {
        "icon": "\U0001F6D1",
        "title": "Trading Has Been Paused",
        "what_happened": "The system detected a serious problem - too many errors in a row, or an unusual "
        "loss pattern - and automatically stopped ALL trading, on every coin, immediately.",
        "why_it_matters": "Continuing to trade while the underlying problem is still unresolved could put "
        "your capital at unnecessary risk. Pausing first and asking questions after is the safer default.",
        "system_action": "Paused all trading across every coin. No new trades will be opened or closed by "
        "the system while this is active.",
        "needs_action": True,
        "action_detail": "Yes - trading stays paused until a person reviews what happened and manually "
        "resumes it. It will not resume on its own, even after a restart.",
    },
    "emergency_flatten": {
        "icon": "\U0001F6A8",
        "title": "All Positions Were Closed",
        "what_happened": "The system sold every open position immediately, all at once.",
        "why_it_matters": "This is a deliberate protective action, not a mistake - when conditions look "
        "risky enough, exiting everything fast matters more than waiting for a better price.",
        "system_action": "Closed every open position at the current market price.",
        "needs_action": True,
        "action_detail": "Yes - review the dashboard to see the reason and outcome for each position that "
        "was closed.",
    },
    "background_loop_failed": {
        "icon": "⚠️",
        "title": "A Background Task Failed",
        "what_happened": "One of the system's behind-the-scenes jobs - retraining a model, running a "
        "strategy tournament, or refreshing which coins to trade - hit an error and stopped early, this "
        "one time.",
        "why_it_matters": "These jobs keep the trading models up to date. A single failed run is usually "
        "harmless, but the same job failing repeatedly could mean models start going stale.",
        "system_action": "Logged the error and will automatically retry on its next scheduled run. Live "
        "trading itself was not interrupted.",
        "needs_action": False,
        "action_detail": "No, not for a single occurrence. Worth a closer look if you see this same job "
        "fail again and again.",
    },
    "feature_drift": {
        "icon": "\U0001F4C9",
        "title": "Market Conditions Have Shifted",
        "what_happened": "A trading model's current market data now looks meaningfully different from the "
        "historical data it was originally trained on.",
        "why_it_matters": "A model trained on one kind of market can make less reliable predictions once "
        "the market starts behaving differently - like using an old map after the roads changed.",
        "system_action": "Kept trading with the existing model, but flagged this for visibility. Drift "
        "alone doesn't automatically stop trading or force a retrain.",
        "needs_action": False,
        "action_detail": "No immediate action - this is an early-warning signal. If real trading results "
        "also start to suffer, a separate check will automatically revert to a better-tested model.",
    },
    "model_decay": {
        "icon": "\U0001F4C9",
        "title": "A Trading Model Is Underperforming Live",
        "what_happened": "A model's real trading results have fallen well below what it achieved during "
        "testing, and its recent real trades have been losing money.",
        "why_it_matters": "A model that no longer matches its own track record could keep losing money if "
        "left in place unchanged.",
        "system_action": "Automatically reverted to the previous, better-tested version of this model to "
        "limit further losses (see the technical details below for whether a rollback happened).",
        "needs_action": False,
        "action_detail": "No immediate action - the system already responded on its own. Worth a closer "
        "look if the same coin keeps triggering this.",
    },
    "stop_loss_triggered": {
        "icon": "\U0001F6E1️",
        "title": "A Protective Stop-Loss Fired",
        "what_happened": "A position was automatically sold to limit how much it could lose.",
        "why_it_matters": "This is the safety net working exactly as designed - it protects your capital "
        "from a larger loss rather than letting a losing position run.",
        "system_action": "Closed the position at its pre-set stop-loss price.",
        "needs_action": False,
        "action_detail": "No - this is routine, expected behavior, not a problem.",
    },
    "trailing_stop_raise_failed": {
        "icon": "⚠️",
        "title": "Couldn't Tighten a Protective Stop",
        "what_happened": "The system tried to raise a position's protective stop-loss - to lock in more "
        "profit as the price rose - but the exchange didn't accept the update.",
        "why_it_matters": "The position is still protected by its OLD stop-loss the whole time - it's just "
        "not as tight as intended, so slightly more profit than ideal could be given back.",
        "system_action": "Left the existing stop-loss in place and will try to raise it again next cycle.",
        "needs_action": False,
        "action_detail": "No, not usually - this often resolves itself. Worth checking your exchange "
        "account if it keeps happening for the same coin.",
    },
    "market_data_integrity": {
        "icon": "⚠️",
        "title": "Suspicious Market Data Detected",
        "what_happened": "The price data coming from the exchange for one coin looked abnormal - for "
        "example, an impossible price jump.",
        "why_it_matters": "Trading on bad data could lead to a bad decision, so the system treats it with "
        "suspicion rather than trusting it blindly.",
        "system_action": "Skipped using that specific data point rather than acting on it.",
        "needs_action": False,
        "action_detail": "No - this is an automatic safeguard working as intended.",
    },
    "regime_churn": {
        "icon": "\U0001F504",
        "title": "Market Trend Is Flip-Flopping",
        "what_happened": "The system's read on whether a coin is trending up, down, or sideways has been "
        "changing very frequently in a short window.",
        "why_it_matters": "A trend label that keeps flipping back and forth is less trustworthy for making "
        "decisions than a stable one.",
        "system_action": "Continued trading, but is treating this coin's current trend label as less "
        "dependable right now.",
        "needs_action": False,
        "action_detail": "No - informational only.",
    },
    "challenger_outperforming": {
        "icon": "\U0001F4C8",
        "title": "A Backup Strategy Is Beating the Current One",
        "what_happened": "In side-by-side paper testing (no real money involved), an alternative trading "
        "strategy is currently outperforming the one actually being used live.",
        "why_it_matters": "Could mean a better strategy is available for this coin than the one currently "
        "trading it.",
        "system_action": "Nothing automatic - this is observation only, no real trades are affected.",
        "needs_action": False,
        "action_detail": "No, not urgent - worth a look out of curiosity, not something that needs a quick "
        "response.",
    },
    "challenger_underperforming": {
        "icon": "\U0001F4C9",
        "title": "A Backup Strategy Is Falling Behind",
        "what_happened": "In side-by-side paper testing (no real money involved), an alternative strategy "
        "is currently trailing the one actually being used live.",
        "why_it_matters": "This actually confirms the current, real strategy is still the better choice for "
        "this coin.",
        "system_action": "Nothing - this is routine monitoring only.",
        "needs_action": False,
        "action_detail": "No action needed.",
    },
}
_DEFAULT_CATEGORY_INFO: Dict[str, Any] = {
    "icon": "\U0001F514",
    "title": "System Alert",
    "what_happened": "The trading system raised an alert that doesn't yet have a plain-language "
    "explanation written for it.",
    "why_it_matters": "See the technical message and details below for exactly what this means.",
    "system_action": "See the technical message below for what, if anything, the system did in response.",
    "needs_action": None,
    "action_detail": "Review the technical details below to decide whether this needs a response.",
}

_SEVERITY_STYLE: Dict[str, Dict[str, str]] = {
    "critical": {"accent": "#DC2626", "tint": "#FEF2F2", "label": "Critical"},
    "warning": {"accent": "#D97706", "tint": "#FFFBEB", "label": "Warning"},
    "info": {"accent": "#2563EB", "tint": "#EFF6FF", "label": "Info"},
}
# For the "Do you need to act?" callout - deliberately separate from
# _SEVERITY_STYLE's colors, since severity (how loud this alert is) and
# whether it needs a human response are different questions - a "critical"
# background_loop_failed still needs no action, and this callout must be
# able to say so in green even inside a red-headered critical card.
_ACTION_STYLE = {
    True: {"bg": "#FEF2F2", "border": "#FCA5A5", "text": "#991B1B", "label": "Yes, action needed"},
    False: {"bg": "#F0FDF4", "border": "#86EFAC", "text": "#166534", "label": "No action needed"},
    None: {"bg": "#F9FAFB", "border": "#E5E7EB", "text": "#374151", "label": "Worth a look"},
}


def _humanize_category(category: str) -> Dict[str, Any]:
    return _CATEGORY_INFO.get(category, _DEFAULT_CATEGORY_INFO)


def _format_context_value(value: Any) -> str:
    text = str(value)
    return text if len(text) <= 300 else text[:300] + "…"


def build_alert_email_text(alert: "Alert") -> str:
    """Plain-text fallback body - shown by clients that don't render HTML,
    and by anything scanning/archiving raw email text."""
    info = _humanize_category(alert.category)
    action = _ACTION_STYLE[info["needs_action"]]
    lines = [
        info["title"],
        "",
        "WHAT HAPPENED",
        info["what_happened"],
        "",
        "WHY IT MATTERS",
        info["why_it_matters"],
        "",
        "WHAT THE SYSTEM DID",
        info["system_action"],
        "",
        f"DO YOU NEED TO ACT? ({action['label']})",
        info["action_detail"],
        "",
        f"Technical message: {alert.message}",
    ]
    if alert.context:
        lines += ["", "Technical context:"]
        lines += [f"  {k}: {_format_context_value(v)}" for k, v in alert.context.items()]
    lines += ["", f"Category: {alert.category}  |  Severity: {alert.severity}"]
    return "\n".join(lines)


def build_alert_email_html(alert: "Alert") -> str:
    """A single self-contained HTML "card" - inline CSS and table-based
    layout only (no <style> block, no external assets), since email
    clients strip or ignore both unpredictably. Structured as 4 plain-
    language questions (what happened / why it matters / what the system
    did / do you need to act) so someone with zero trading background can
    follow it section by section, with the exact technical message and
    context still shown in full below for anyone who wants the specifics."""
    info = _humanize_category(alert.category)
    style = _SEVERITY_STYLE.get(alert.severity, _SEVERITY_STYLE["info"])
    action = _ACTION_STYLE[info["needs_action"]]
    esc = _html.escape

    def section(label: str, body: str) -> str:
        return f"""
            <tr>
              <td style="padding:16px 28px 0 28px;">
                <div style="font-size:11px;font-weight:700;color:#6B7280;text-transform:uppercase;
                            letter-spacing:0.06em;margin-bottom:5px;">{esc(label)}</div>
                <p style="font-size:14.5px;line-height:1.6;color:#374151;margin:0;">{esc(body)}</p>
              </td>
            </tr>"""

    context_rows = ""
    if alert.context:
        rows = "".join(
            f'<tr>'
            f'<td style="padding:6px 10px;color:#6B7280;font-size:13px;font-family:ui-monospace,'
            f'SFMono-Regular,Menlo,Consolas,monospace;white-space:nowrap;vertical-align:top;">{esc(str(k))}</td>'
            f'<td style="padding:6px 10px;color:#111827;font-size:13px;font-family:ui-monospace,'
            f'SFMono-Regular,Menlo,Consolas,monospace;word-break:break-word;">{esc(_format_context_value(v))}</td>'
            f'</tr>'
            for k, v in alert.context.items()
        )
        context_rows = f"""
        <tr><td style="padding:20px 28px 0 28px;">
          <div style="font-size:12px;font-weight:600;color:#6B7280;text-transform:uppercase;
                      letter-spacing:0.05em;margin-bottom:6px;">Technical details</div>
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                 style="background:#F9FAFB;border-radius:8px;border:1px solid #F3F4F6;">
            {rows}
          </table>
        </td></tr>"""

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return f"""\
<!doctype html>
<html>
  <body style="margin:0;padding:24px 12px;background:#F3F4F6;
               font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
      <tr>
        <td align="center">
          <table role="presentation" width="560" cellpadding="0" cellspacing="0"
                 style="max-width:560px;width:100%;background:#FFFFFF;border-radius:12px;overflow:hidden;
                        border:1px solid #E5E7EB;">
            <tr>
              <td style="background:{style['tint']};border-bottom:3px solid {style['accent']};padding:18px 28px;">
                <span style="display:inline-block;font-size:11px;font-weight:700;letter-spacing:0.08em;
                             text-transform:uppercase;color:{style['accent']};">
                  {style['label']} Alert &middot; Autonomous Trading Platform
                </span>
              </td>
            </tr>
            <tr>
              <td style="padding:28px 28px 4px 28px;">
                <div style="font-size:22px;">{info['icon']}</div>
                <div style="font-size:20px;font-weight:700;color:#111827;margin-top:8px;line-height:1.3;">
                  {esc(info['title'])}
                </div>
              </td>
            </tr>
            {section("What happened", info["what_happened"])}
            {section("Why it matters", info["why_it_matters"])}
            {section("What the system did", info["system_action"])}
            <tr>
              <td style="padding:20px 28px 0 28px;">
                <div style="background:{action['bg']};border:1px solid {action['border']};border-radius:8px;
                            padding:14px 16px;">
                  <div style="font-size:12px;font-weight:700;color:{action['text']};text-transform:uppercase;
                              letter-spacing:0.05em;margin-bottom:4px;">
                    Do you need to act? &mdash; {esc(action['label'])}
                  </div>
                  <div style="font-size:14px;line-height:1.5;color:{action['text']};">
                    {esc(info['action_detail'])}
                  </div>
                </div>
              </td>
            </tr>
            <tr>
              <td style="padding:20px 28px 0 28px;">
                <div style="font-size:11px;font-weight:700;color:#6B7280;text-transform:uppercase;
                            letter-spacing:0.06em;margin-bottom:5px;">Technical message</div>
                <div style="background:#F9FAFB;border-left:3px solid {style['accent']};border-radius:6px;
                            padding:12px 16px;font-size:14px;color:#111827;line-height:1.5;">
                  {esc(alert.message)}
                </div>
              </td>
            </tr>
            {context_rows}
            <tr>
              <td style="padding:24px 28px 24px 28px;">
                <div style="border-top:1px solid #F3F4F6;padding-top:16px;font-size:12px;color:#9CA3AF;">
                  {esc(timestamp)} &middot; category: {esc(alert.category)} &middot; severity: {esc(alert.severity)}
                  <br>Automated alert from your trading system - also recorded permanently in its dashboard.
                </div>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""


@dataclass
class Alert:
    category: str
    severity: str
    message: str
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "severity": self.severity,
            "message": self.message,
            "context": self.context,
        }


class AlertManager:
    def __init__(
        self,
        journal: Optional[Journal] = None,
        webhook_url: Optional[str] = None,
        webhook_timeout_seconds: float = 5.0,
        webhook_cooldown_seconds: float = 3600.0,
        # Email delivery via SMTP - defaults match Gmail (smtp.gmail.com,
        # port 587, STARTTLS) since that's the provider this system
        # standardizes on, but any STARTTLS-speaking SMTP server works.
        # None for smtp_username/smtp_password/email_to (any of them)
        # disables email delivery entirely (opt-in, same convention as the
        # webhook above).
        smtp_host: str = "smtp.gmail.com",
        smtp_port: int = 587,
        smtp_username: Optional[str] = None,
        smtp_password: Optional[str] = None,
        email_from: Optional[str] = None,
        email_to: Optional[List[str]] = None,
        email_timeout_seconds: float = 10.0,
        email_cooldown_seconds: float = 3600.0,
        # Only critical alerts email by default - webhook/journal already
        # carry everything, and email is the channel most likely to page a
        # phone, so routine warnings (drift, regime churn) shouldn't ring
        # someone's phone at 3am the way a circuit-breaker trip should.
        email_min_severity: str = "critical",
    ):
        if email_min_severity not in SEVERITIES:
            raise ValueError(f"email_min_severity must be one of {SEVERITIES}, got {email_min_severity!r}")
        self.journal = journal
        self.webhook_url = webhook_url
        self.webhook_timeout_seconds = webhook_timeout_seconds
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_username = smtp_username
        self.smtp_password = smtp_password
        self.email_from = email_from or smtp_username
        self.email_to = email_to or []
        self.email_timeout_seconds = email_timeout_seconds
        self.email_min_severity = email_min_severity
        # Applies ONLY to external delivery, never to the journal record
        # (every occurrence is always persisted, in full, for the audit
        # trail) - a persistent condition checked every cycle (e.g. feature
        # drift) would otherwise page a human every single cycle for as
        # long as it remains true, which trains people to ignore the
        # channel entirely. Webhook and email are tracked independently -
        # suppressing one must never silently suppress the other.
        self.webhook_cooldown_seconds = webhook_cooldown_seconds
        self.email_cooldown_seconds = email_cooldown_seconds
        self._last_sent: Dict[tuple, float] = {}
        # Tracks background delivery threads (see raise_alert) purely so
        # tests can deterministically wait for one to finish rather than
        # racing it - wait_for_pending_alerts() below. Production code
        # never needs to touch this; delivery is genuinely fire-and-forget.
        self._pending_alert_threads: List[threading.Thread] = []

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_username and self.smtp_password and self.email_to)

    def wait_for_pending_alerts(self, timeout: float = 5.0) -> None:
        """Block until every background alert-delivery thread spawned so
        far has finished. For tests only - real callers never need this,
        since delivery is deliberately fire-and-forget (see raise_alert)."""
        for thread in self._pending_alert_threads:
            thread.join(timeout=timeout)
        self._pending_alert_threads = [t for t in self._pending_alert_threads if t.is_alive()]

    def raise_alert(
        self,
        category: str,
        severity: str,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        dedupe_key: Optional[str] = None,
    ) -> Alert:
        if severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}, got {severity!r}")

        alert = Alert(category=category, severity=severity, message=message, context=context or {})

        log_fn = {"info": logger.info, "warning": logger.warning, "critical": logger.error}[severity]
        log_fn(f"ALERT [{category}] {message}", extra={"context": alert.context})

        if self.journal is not None:
            self.journal.record_alert(
                category=category, severity=severity, message=message, context=alert.context
            )

        # Dispatched on a background thread, not called inline: a real
        # measured SMTP round-trip (connect + STARTTLS + login + send) takes
        # ~3 seconds - raise_alert() is called from deep inside the live
        # per-symbol cycle loop (run_cycle iterates symbols in a plain
        # sequential for-loop), so calling _send_email/_send_webhook
        # directly here would stall EVERY remaining symbol's real trading
        # evaluation for that long, every single time a critical alert
        # fires - worse with multiple alerts in the same cycle, since each
        # would block in turn. External notification delivery must never
        # be capable of delaying real trading decisions. daemon=True: if
        # the process shuts down mid-send, the alert is already durably
        # journaled above (the audit record never depends on this) - only
        # the external notification itself could be lost, an acceptable
        # trade against blocking a clean, bounded shutdown.
        if self.webhook_url and self._should_send("webhook", dedupe_key, self.webhook_cooldown_seconds):
            thread = threading.Thread(target=self._send_webhook, args=(alert,), daemon=True)
            thread.start()
            self._pending_alert_threads.append(thread)

        if (
            self.email_configured
            and SEVERITIES.index(severity) >= SEVERITIES.index(self.email_min_severity)
            and self._should_send("email", dedupe_key, self.email_cooldown_seconds)
        ):
            thread = threading.Thread(target=self._send_email, args=(alert,), daemon=True)
            thread.start()
            self._pending_alert_threads.append(thread)

        return alert

    def _should_send(self, channel: str, dedupe_key: Optional[str], cooldown_seconds: float) -> bool:
        """No dedupe_key means the caller considers this event inherently
        singular (e.g. a circuit breaker trip, already only fires once per
        trip) - always send. With a dedupe_key, suppress a repeat send on
        THIS channel for the same key within cooldown_seconds - tracked
        per (channel, dedupe_key) so throttling one channel never silently
        throttles the other.

        Journal-backed when a journal is configured (the live/production
        case) so the cooldown survives a restart - a crash-restart loop
        (scripts/run_forever.sh retries every 15s) used to reset the
        in-memory cooldown on every attempt, so a persistent failure could
        spam a webhook/email once per restart instead of respecting
        cooldown_seconds across the whole episode. Falls back to the
        in-memory dict when no journal is available (matches the original,
        process-lifetime-only behavior - some callers/tests deliberately
        construct an AlertManager with journal=None)."""
        if dedupe_key is None:
            return True
        if self.journal is not None:
            return self._should_send_persisted(channel, dedupe_key, cooldown_seconds)
        key = (channel, dedupe_key)
        now = time.monotonic()
        last_sent = self._last_sent.get(key)
        if last_sent is not None and now - last_sent < cooldown_seconds:
            return False
        self._last_sent[key] = now
        return True

    def _should_send_persisted(self, channel: str, dedupe_key: str, cooldown_seconds: float) -> bool:
        last_sent_str = self.journal.last_alert_delivery(channel=channel, dedupe_key=dedupe_key)
        if last_sent_str is not None:
            last_sent = datetime.fromisoformat(last_sent_str)
            if last_sent.tzinfo is None:
                last_sent = last_sent.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - last_sent).total_seconds() < cooldown_seconds:
                return False
        self.journal.record_alert_delivery(channel=channel, dedupe_key=dedupe_key)
        return True

    def _send_webhook(self, alert: Alert) -> None:
        # A webhook delivery failure (network blip, revoked URL, rate
        # limit) must never interrupt the trading loop that raised this
        # alert - it's already durably persisted to the journal above
        # regardless of whether this best-effort external notification
        # succeeds.
        try:
            requests.post(
                self.webhook_url,
                json={"text": f"[{alert.severity.upper()}] {alert.category}: {alert.message}"},
                timeout=self.webhook_timeout_seconds,
            )
        except Exception:  # noqa: BLE001
            logger.exception("failed to deliver alert webhook", extra={"context": {"category": alert.category}})

    def _send_email(self, alert: Alert) -> None:
        # Same best-effort guarantee as the webhook above - an SMTP outage
        # or a revoked app password must never interrupt the trading loop.
        try:
            info = _humanize_category(alert.category)
            email = EmailMessage()
            email["Subject"] = f"[{alert.severity.upper()}] {info['icon']} {info['title']} ({alert.category})"
            email["From"] = self.email_from
            email["To"] = ", ".join(self.email_to)
            # Plain text first (the actual body for non-HTML clients), then
            # add_alternative for HTML - this makes the message
            # multipart/alternative so an HTML-capable client (the common
            # case - Gmail web/mobile) renders the card, while anything
            # else still gets a fully readable plain-text version. Never
            # HTML-only: a malformed template must not leave a client that
            # can't render it with an empty email.
            email.set_content(build_alert_email_text(alert))
            email.add_alternative(build_alert_email_html(alert), subtype="html")

            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=self.email_timeout_seconds) as server:
                server.starttls()
                server.login(self.smtp_username, self.smtp_password)
                server.send_message(email)
        except Exception:  # noqa: BLE001
            logger.exception("failed to deliver alert email", extra={"context": {"category": alert.category}})
