"""Host-owned registry for Sessions whose physical cleanup needs a retry."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from artemis.runtime.cleanup_errors import CleanupErrorCapture

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
        return str(getattr(session, "runtime_id", None) or session.session_id or id(session))

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

    async def manual_recover(self, session_id: str) -> SessionCloseReport:
        """Operator path for a ``MANUAL_REQUIRED`` session.

        Automatic ``retry_pending`` deliberately skips these; an operator must
        inspect the retained resource report and explicitly invoke this method.
        """

        report = self.get_report(session_id)
        if report is not None and report.cleanup_state.value == "complete":
            return report
        session = self._sessions.get(str(session_id))
        if session is None:
            raise KeyError(session_id)
        capture = CleanupErrorCapture()
        with capture:
            report = await self.retry(session_id)
        if capture.error is not None:
            report = session.mark_manual_required(f"manual recovery failed: {capture.error}")
            self._reports[str(session_id)] = report
            return report
        if not report.cleanup_complete:
            report = session.mark_manual_required("manual recovery still incomplete")
            self._reports[str(session_id)] = report
        return report

    async def retry_pending(
        self,
        *,
        max_attempts: int = 1,
        timeout_s: float = 10.0,
    ) -> list[SessionCloseReport]:
        """Bounded Host-runtime recovery; never retries a manual-only session."""

        results: list[SessionCloseReport] = []
        for session_id in list(self._sessions):
            session = self._sessions.get(session_id)
            if session is None:
                continue
            report = self.get_report(session_id)
            if report is not None and report.cleanup_state.value == "manual_required":
                results.append(report)
                continue
            for _ in range(max(1, int(max_attempts))):
                capture = CleanupErrorCapture()
                with capture:
                    report = await asyncio.wait_for(self.retry(session_id), timeout=timeout_s)
                if capture.error is not None:
                    report = session.mark_manual_required(f"retry failed: {capture.error}")
                    self._reports[session_id] = report
                    break
                self._reports[session_id] = report
                if report.cleanup_complete:
                    break
            if report is not None and not report.cleanup_complete:
                report = session.mark_manual_required(
                    f"cleanup still incomplete after {max_attempts} attempt(s)"
                )
                self._reports[session_id] = report
            if report is not None:
                results.append(report)
        return results

    def get_report(self, session_id: str) -> SessionCloseReport | None:
        return self._reports.get(str(session_id))
