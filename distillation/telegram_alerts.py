from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from typing import Callable, Mapping, Protocol, Sequence

import httpx


TELEGRAM_API_BASE_URL = "https://api.telegram.org"
TELEGRAM_MESSAGE_LIMIT = 4096

log = logging.getLogger(__name__)


class AlarmEdgeDetector:
    """Rising-edge detector with per-alarm throttling."""

    def __init__(
        self,
        *,
        throttle_seconds: float = 60.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._throttle_seconds = float(throttle_seconds)
        self._clock = clock
        self._previous: dict[str, bool] = {}
        self._last_fired: dict[str, float] = {}

    def seed(self, snapshot: Mapping[str, bool]) -> None:
        for alarm, active in snapshot.items():
            self._previous[str(alarm)] = bool(active)

    def fresh_trips(
        self,
        active_alarms: Sequence[str],
        watched_alarms: Sequence[str],
    ) -> list[str]:
        active = {str(alarm) for alarm in active_alarms}
        fresh: list[str] = []
        for alarm in watched_alarms:
            if self._update_one(str(alarm), str(alarm) in active):
                fresh.append(str(alarm))
        return fresh

    def _update_one(self, alarm: str, active: bool) -> bool:
        was_active = self._previous.get(alarm, False)
        self._previous[alarm] = bool(active)
        if not active or was_active:
            return False

        now = self._clock()
        last_fired = self._last_fired.get(alarm)
        if last_fired is not None and now - last_fired < self._throttle_seconds:
            return False
        self._last_fired[alarm] = now
        return True


@dataclass(frozen=True)
class TelegramSendResult:
    sent: bool
    dry_run: bool = False
    status_code: int | None = None
    message: str = ""


class TelegramAlertSender:
    def __init__(
        self,
        *,
        bot_token: str | None,
        chat_id: str | None,
        dry_run: bool = False,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.bot_token = (bot_token or "").strip()
        self.chat_id = (chat_id or "").strip()
        self.dry_run = bool(dry_run)
        self.timeout = float(timeout)
        self.transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    @property
    def send_message_url(self) -> str:
        return f"{TELEGRAM_API_BASE_URL}/bot{self.bot_token}/sendMessage"

    def send(self, text: str) -> TelegramSendResult:
        if self.dry_run or not self.configured:
            log.info("[Telegram dry-run] chat=%s text=%r", self.chat_id or "<unset>", text)
            return TelegramSendResult(sent=True, dry_run=True, message="dry-run")

        ok = True
        last_status: int | None = None
        last_message = ""
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                for chunk in _message_chunks(text):
                    response = client.post(
                        self.send_message_url,
                        json={"chat_id": self.chat_id, "text": chunk},
                    )
                    last_status = response.status_code
                    if response.status_code >= 300:
                        ok = False
                        last_message = response.text[:300]
                        log.warning("Telegram send failed with HTTP %s: %s", response.status_code, last_message)
        except httpx.HTTPError as exc:
            return TelegramSendResult(sent=False, status_code=last_status, message=str(exc))

        return TelegramSendResult(sent=ok, status_code=last_status, message=last_message)


class AlarmSender(Protocol):
    def send(self, text: str):
        ...


def _message_chunks(text: str) -> list[str]:
    return [
        text[index : index + TELEGRAM_MESSAGE_LIMIT]
        for index in range(0, len(text), TELEGRAM_MESSAGE_LIMIT)
    ] or [""]


def format_alarm_message(
    alarm: str,
    *,
    label: str | None = None,
    mode: str | None = None,
    timestamp: str | None = None,
) -> str:
    lines = [
        "[DT101 ALARM]",
        f"Alarm: {label or alarm}",
        f"Tag: {alarm}",
    ]
    if mode:
        lines.append(f"Mode: {mode}")
    if timestamp:
        lines.append(f"Time: {timestamp}")
    return "\n".join(lines)


def send_fresh_alarm_alerts(
    *,
    detector: AlarmEdgeDetector,
    sender: AlarmSender,
    active_alarms: Sequence[str],
    watched_alarms: Sequence[str],
    labels: Mapping[str, str] | None = None,
    mode: str | None = None,
    timestamp: str | None = None,
) -> list[str]:
    label_map = labels or {}
    messages: list[str] = []
    for alarm in detector.fresh_trips(active_alarms, watched_alarms):
        message = format_alarm_message(
            alarm,
            label=label_map.get(alarm),
            mode=mode,
            timestamp=timestamp,
        )
        sender.send(message)
        messages.append(message)
    return messages
