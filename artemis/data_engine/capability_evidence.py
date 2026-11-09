"""Global, append-only capability evidence store (ADR-008).

Execution traces live in the run's Data Engine DB; capability evidence is
cross-run and therefore lives in its own global SQLite file under the app dir.
It is deliberately *not* derived from ``--traces-path``: a verification result
must survive the run directory that produced it.

Writes are synchronous: a caller may treat "record returned" as durable before
marking a capability ``verified``.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any, Iterable, Literal, Mapping, Sequence, cast

from artemis.config.paths import get_app_dir
from artemis.core.capabilities import CapabilityEvidence, canonical_endpoint_ids
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)

__all__ = [
    "CapabilityArtifactStore",
    "SqliteCapabilityEvidenceStore",
    "capability_evidence_enabled",
    "default_evidence_dir",
]

ENV_DB = "ARTEMIS_CAPABILITY_DB"
ENV_ARTIFACTS = "ARTEMIS_CAPABILITY_ARTIFACTS"
ENV_EVIDENCE = "ARTEMIS_CAPABILITY_EVIDENCE"
MAX_ARTIFACT_BYTES = 5 * 1024 * 1024


def capability_evidence_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Global evidence store switch; enabled unless explicitly disabled."""

    values = os.environ if env is None else env
    value = (values.get(ENV_EVIDENCE) or "1").strip().lower()
    return value not in {"0", "false", "off", "no"}


def default_evidence_dir() -> Path:
    return get_app_dir() / "capabilities"


def _sqlite_path(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


class CapabilityArtifactStore:
    """Content-addressed artifacts for evidence (5 MB cap, no traversal)."""

    max_bytes = MAX_ARTIFACT_BYTES

    def __init__(self, directory: Path | str | None = None) -> None:
        override = (os.environ.get(ENV_ARTIFACTS) or "").strip()
        self.directory = Path(directory or override or (default_evidence_dir() / "artifacts"))
        self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _clean_ref(ref: str) -> str:
        text = str(ref or "").strip().lower()
        if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
            raise ValueError(f"invalid artifact ref {ref!r}")
        return text

    def put(self, data: bytes, *, kind: str, content_type: str = "application/octet-stream") -> str:
        payload = bytes(data)
        if len(payload) > self.max_bytes:
            raise ValueError(f"artifact is {len(payload)} bytes; the cap is {self.max_bytes}")
        digest = hashlib.sha256(payload).hexdigest()
        target = self.directory / digest
        if not target.exists():
            temp = target.with_name(f".{digest}.tmp")
            temp.write_bytes(payload)
            os.replace(temp, target)
        meta = {
            "sha256": digest,
            "size": len(payload),
            "kind": str(kind),
            "content_type": str(content_type),
            "created_at": datetime.now(UTC).isoformat(),
        }
        meta_path = self.directory / f"{digest}.json"
        if not meta_path.exists():
            meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return digest

    def path(self, ref: str) -> Path:
        return self.directory / self._clean_ref(ref)

    def exists(self, ref: str) -> bool:
        return self.path(ref).exists()

    def metadata(self, ref: str) -> dict[str, Any] | None:
        path = self.directory / f"{self._clean_ref(ref)}.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None


class SqliteCapabilityEvidenceStore:
    """Append-only evidence + lookup by capability/device/endpoints/fingerprint."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        artifacts: CapabilityArtifactStore | None = None,
    ) -> None:
        override = (os.environ.get(ENV_DB) or "").strip()
        self.db_path = Path(db_path or override or (default_evidence_dir() / "evidence.db"))
        self.artifacts = artifacts or CapabilityArtifactStore()
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(_sqlite_path(self.db_path), timeout=15.0)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _init_db(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS capability_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL,
                    capability_id TEXT NOT NULL,
                    conclusion TEXT NOT NULL CHECK (conclusion IN ('verified','failed')),
                    device_id TEXT NOT NULL,
                    endpoint_ids TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    fingerprint_fields TEXT NOT NULL,
                    test_id TEXT NOT NULL,
                    test_version TEXT NOT NULL,
                    evidence_kind TEXT NOT NULL CHECK (
                        evidence_kind IN ('contract_test','smoke','manual')
                    ),
                    executed_at REAL NOT NULL,
                    environment TEXT NOT NULL,
                    artifact_refs TEXT NOT NULL,
                    operator TEXT,
                    notes TEXT,
                    revalidate_after REAL,
                    superseded_by TEXT,
                    created_at REAL NOT NULL
                )
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS capability_evidence_lookup
                ON capability_evidence (
                    capability_id, device_id, fingerprint, executed_at
                )
            """)

    @staticmethod
    def _endpoint_key(endpoint_ids: Iterable[str]) -> str:
        return json.dumps(sorted(canonical_endpoint_ids(tuple(endpoint_ids))))

    def record(self, evidence: CapabilityEvidence) -> None:
        for ref in evidence.artifact_refs:
            if not self.artifacts.exists(ref):
                raise ValueError(f"evidence artifact {ref!r} is not stored")
        endpoint_key = self._endpoint_key(evidence.endpoint_ids)
        created_at = datetime.now(UTC).timestamp()
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM capability_evidence WHERE evidence_id = ?",
                (evidence.evidence_id,),
            ).fetchone()
            if existing is not None:
                return  # idempotent by evidence id
            connection.execute(
                """
                UPDATE capability_evidence
                   SET superseded_by = ?
                 WHERE capability_id = ?
                   AND device_id = ?
                   AND endpoint_ids = ?
                   AND fingerprint = ?
                   AND evidence_id <> ?
                   AND superseded_by IS NULL
                   AND executed_at <= ?
                """,
                (
                    evidence.evidence_id,
                    evidence.capability_id,
                    evidence.device_id,
                    endpoint_key,
                    evidence.fingerprint,
                    evidence.evidence_id,
                    evidence.executed_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO capability_evidence (
                    evidence_id, schema_version, capability_id, conclusion, device_id,
                    endpoint_ids, fingerprint, fingerprint_fields, test_id, test_version,
                    evidence_kind, executed_at, environment, artifact_refs, operator,
                    notes, revalidate_after, superseded_by, created_at
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    evidence.evidence_id,
                    evidence.capability_id,
                    evidence.conclusion,
                    evidence.device_id,
                    endpoint_key,
                    evidence.fingerprint,
                    json.dumps(dict(evidence.fingerprint_fields), ensure_ascii=False),
                    evidence.test_id,
                    evidence.test_version,
                    evidence.evidence_kind,
                    evidence.executed_at,
                    json.dumps(dict(evidence.environment), ensure_ascii=False),
                    json.dumps(list(evidence.artifact_refs), ensure_ascii=False),
                    evidence.operator,
                    evidence.notes,
                    evidence.revalidate_after,
                    created_at,
                ),
            )
            connection.commit()

    def lookup(
        self,
        *,
        capability_id: str,
        device_id: str,
        endpoint_ids: Sequence[str],
        fingerprint: str,
    ) -> CapabilityEvidence | None:
        endpoint_key = self._endpoint_key(endpoint_ids)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT evidence_id, capability_id, conclusion, device_id, endpoint_ids,
                       fingerprint, fingerprint_fields, test_id, test_version, evidence_kind,
                       executed_at, environment, artifact_refs, operator, notes,
                       revalidate_after, superseded_by
                  FROM capability_evidence
                 WHERE capability_id = ?
                   AND device_id = ?
                   AND endpoint_ids = ?
                   AND fingerprint = ?
                   AND superseded_by IS NULL
                 ORDER BY executed_at DESC, created_at DESC
                 LIMIT 1
                """,
                (str(capability_id), str(device_id), endpoint_key, str(fingerprint)),
            ).fetchone()
        return self._row_to_evidence(row) if row is not None else None

    def list(
        self,
        *,
        device_id: str | None = None,
        capability_id: str | None = None,
        include_superseded: bool = False,
    ) -> tuple[CapabilityEvidence, ...]:
        query = (
            "SELECT evidence_id, capability_id, conclusion, device_id, endpoint_ids, "
            "fingerprint, fingerprint_fields, test_id, test_version, evidence_kind, "
            "executed_at, environment, artifact_refs, operator, notes, revalidate_after, "
            "superseded_by FROM capability_evidence WHERE 1=1"
        )
        params: list[Any] = []
        if device_id:
            query += " AND device_id = ?"
            params.append(device_id)
        if capability_id:
            query += " AND capability_id = ?"
            params.append(capability_id)
        if not include_superseded:
            query += " AND superseded_by IS NULL"
        query += " ORDER BY executed_at ASC"
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(self._row_to_evidence(row) for row in rows)

    @staticmethod
    def _row_to_evidence(row: Sequence[Any]) -> CapabilityEvidence:
        return CapabilityEvidence(
            evidence_id=str(row[0]),
            capability_id=str(row[1]),
            conclusion=cast(Literal["verified", "failed"], str(row[2])),
            device_id=str(row[3]),
            endpoint_ids=tuple(json.loads(row[4])),
            fingerprint=str(row[5]),
            fingerprint_fields=dict(json.loads(row[6])),
            test_id=str(row[7]),
            test_version=str(row[8]),
            evidence_kind=cast(Literal["contract_test", "smoke", "manual"], str(row[9])),
            executed_at=float(row[10]),
            environment=dict(json.loads(row[11])),
            artifact_refs=tuple(json.loads(row[12])),
            operator=row[13],
            notes=row[14],
            revalidate_after=row[15],
            superseded_by=row[16],
        )
