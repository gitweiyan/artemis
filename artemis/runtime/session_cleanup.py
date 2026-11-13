"""Host-owned registry for Sessions whose physical cleanup needs a retry."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from artemis.devices.session import DeviceSession, SessionCloseReport

__all__ = ["SessionCleanupRegistry"]


class SessionCleanupRegistry:
    """Retain incomplete Sessions without creating a global singleton."""

    def __init__(self) -> None:
        self._sessions: dict[str, DeviceSession] = {}
        self._reports: dict[str, SessionCloseReport] = {}

    @staticmethod
    def _key(session: DeviceSession) -> str:
        return str(session.session_id or id(session))

    def register(self, session: DeviceSession) -> None:
        key = self._key(session)
        existing = self._sessions.get(key)
        if existing is not None and existing is not session:
            raise ValueError(f"cleanup registry already owns session {key!r}")
        self._sessions[key] = session
        report = getattr(session, "last_close_report", None)
        if report is not None:
            self._reports[key] = report

    def pending(self) -> list[SessionCloseReport]:
        return [report for key, report in self._reports.items() if key in self._sessions]

    async def retry(self, session_id: str) -> SessionCloseReport:
        session = self._sessions.get(str(session_id))
        if session is None:
            raise KeyError(session_id)
        report = await session.retry_cleanup()
        self._reports[str(session_id)] = report
        if report.cleanup_complete:
            self._sessions.pop(str(session_id), None)
        return report

    def get_report(self, session_id: str) -> SessionCloseReport | None:
        return self._reports.get(str(session_id))
