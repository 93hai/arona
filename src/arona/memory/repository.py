# Arona 3.0 Memory Repository

from __future__ import annotations

import re
import sqlite3
from heapq import nsmallest
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Optional, Sequence

from arona.database.connection import DatabaseConnection
from arona.domain.models import MemoryRecord, MemorySensitivity, MemoryStatus, MemoryType
from arona.memory.keys import normalize_content, subject_key
from arona.memory.redaction import contains_secret, validate_identifier

class MemoryRepository:
    """Repository cho các thao tác memory trong Arona 3.0.
    
    Class này chịu trách nhiệm quản lý các thao tác cơ sở dữ liệu liên quan đến
    memory, bao gồm tạo, đọc, cập nhật và xóa. Nó không chứa logic AI, Discord
    hoặc Policy Engine.
    """
    
    def __init__(self, db: DatabaseConnection) -> None:
        """Khởi tạo MemoryRepository với DatabaseConnection.
        
        Args:
            db: DatabaseConnection đã cấu hình.
        """
        self.db = db
        self._connection = None

    @contextmanager
    def atomic(self):
        """One write transaction for a complete Memory operation, including its audit."""
        if self._connection is not None:
            raise RuntimeError("Nested Memory operations are not supported")
        with self.db.transaction() as conn:
            scoped = MemoryRepository(self.db)
            scoped._connection = conn
            try:
                yield scoped
            finally:
                scoped._connection = None

    @contextmanager
    def _transaction(self, *, write=True):
        if self._connection is not None:
            yield self._connection
        else:
            with self.db.transaction(write=write) as conn:
                yield conn

    @staticmethod
    def _validated(memory: MemoryRecord) -> MemoryRecord:
        memory = MemoryRecord.model_validate(memory.model_dump())
        for value in (memory.memory_id, memory.owner_id, memory.workspace_id):
            validate_identifier(value)
        if (not memory.content.strip() or not memory.source.strip()
            or len(memory.content) > 10000 or len(memory.source) > 1000
            or memory.sensitivity == MemorySensitivity.SECRET
            or contains_secret(memory.content) or contains_secret(memory.source)):
            raise ValueError("Memory data must be bounded, nonempty and free of credentials")
        return memory

    @staticmethod
    def _event(conn, owner_id, workspace_id, action, memory_id=None, count=1):
        conn.execute(
            "INSERT INTO memory_events(owner_id,workspace_id,action,memory_id,affected_count) VALUES (?,?,?,?,?)",
            (owner_id, workspace_id, action, memory_id, count),
        )

    @staticmethod
    def _write_keys(conn, memory_id: str, content: str) -> None:
        conn.execute(
            "UPDATE memory_lookup SET normalized_content=?, subject_key=? WHERE memory_id=?",
            (normalize_content(content), subject_key(content), memory_id),
        )

    def admission_candidates(
        self, owner_id: str, workspace_id: str, memory_type: MemoryType, content: str,
    ) -> list[MemoryRecord]:
        """Indexed exact keys; preserve the active-list order and expiry semantics."""
        validate_identifier(owner_id)
        validate_identifier(workspace_id)
        with self._transaction() as conn:
            pending = conn.execute(
                "SELECT k.memory_id,m.content FROM memory_lookup k "
                "JOIN memories m ON m.memory_id=k.memory_id "
                "WHERE k.owner_id=? AND k.workspace_id=? AND k.normalized_content IS NULL",
                (owner_id, workspace_id),
            ).fetchall()
            for row in pending:
                self._write_keys(conn, row["memory_id"], row["content"])
            query = (
                "SELECT memory_id FROM memory_lookup WHERE owner_id=? AND workspace_id=? "
                "AND memory_type=? AND normalized_content=?"
            )
            params = [owner_id, workspace_id, memory_type.name, normalize_content(content)]
            subject = subject_key(content)
            if subject:
                query += (
                    " UNION SELECT memory_id FROM memory_lookup "
                    "WHERE owner_id=? AND workspace_id=? AND subject_key=?"
                )
                params.extend((owner_id, workspace_id, subject))
            rows = conn.execute(
                "SELECT m.* FROM (" + query + ") AS candidates "
                "CROSS JOIN memories m ON m.memory_id=candidates.memory_id "
                "WHERE m.owner_id=? AND m.workspace_id=? AND m.status='ACTIVE' "
                "AND (m.expires_at IS NULL OR julianday(m.expires_at)>julianday('now')) "
                "ORDER BY m.updated_at DESC, m.memory_id",
                (*params, owner_id, workspace_id),
            )
            return [self._row_to_memory_record(row) for row in rows]

    def create(self, memory: MemoryRecord) -> MemoryRecord:
        """Tạo một memory mới trong cơ sở dữ liệu.
        
        Args:
            memory: MemoryRecord chứa dữ liệu memory mới.
        
        Returns:
            MemoryRecord: Bản ghi memory đã được tạo.
        
        Raises:
            ValueError: Nếu memory_id đã tồn tại.
        """
        memory = self._validated(memory)
        with self._transaction() as conn:
            cursor = conn.cursor()
            
            # Kiểm tra xem memory_id đã tồn tại chưa
            cursor.execute(
                """
                SELECT memory_id FROM memories
                WHERE memory_id = ?
                """,
                (memory.memory_id,),
            )
            if cursor.fetchone():
                raise ValueError(
                    "Memory ID already exists"
                )
            
            # Chèn bản ghi mới
            cursor.execute(
                """
                INSERT INTO memories (
                    memory_id, owner_id, workspace_id, memory_type,
                    content, source, created_at, updated_at,
                    confidence, sensitivity, expires_at, status, version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory.memory_id,
                    memory.owner_id,
                    memory.workspace_id,
                    memory.memory_type.name,
                    memory.content,
                    memory.source,
                    memory.created_at.isoformat(" "),
                    memory.updated_at.isoformat(" "),
                    memory.confidence,
                    memory.sensitivity.name,
                    memory.expires_at.isoformat(" ") if memory.expires_at else None,
                    memory.status.name,
                    memory.version,
                ),
            )
            
            self._write_keys(conn, memory.memory_id, memory.content)
            self._event(conn, memory.owner_id, memory.workspace_id, "create", memory.memory_id)
            # Trả về bản ghi đã tạo
            return self._get_by_id(
                conn,
                memory.memory_id,
                memory.owner_id,
                memory.workspace_id,
            )

    def get_by_id(
        self,
        memory_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> Optional[MemoryRecord]:
        """Lấy một memory theo ID.
        
        Args:
            memory_id: ID của memory.
            owner_id: ID của chủ sở hữu.
            workspace_id: ID của workspace.
        
        Returns:
            Optional[MemoryRecord]: Bản ghi memory nếu tồn tại, ngược lại None.
        """
        validate_identifier(owner_id)
        validate_identifier(workspace_id)
        with self._transaction(write=False) as conn:
            return self._get_by_id(conn, memory_id, owner_id, workspace_id)

    def _get_by_id(self, conn, memory_id, owner_id, workspace_id):
        row = conn.execute(
            "SELECT * FROM memories WHERE memory_id=? AND owner_id=? AND workspace_id=?",
            (memory_id, owner_id, workspace_id),
        ).fetchone()
        return self._row_to_memory_record(row) if row else None

    def update(self, memory: MemoryRecord) -> MemoryRecord:
        """Cập nhật một memory trong cơ sở dữ liệu.
        
        Args:
            memory: MemoryRecord chứa dữ liệu cập nhật.
        
        Returns:
            MemoryRecord: Bản ghi memory đã được cập nhật.
        
        Raises:
            ValueError: Nếu memory không tồn tại.
        """
        memory = self._validated(memory)
        with self._transaction() as conn:
            cursor = conn.cursor()
            
            # Kiểm tra xem memory có tồn tại không
            cursor.execute(
                """
                SELECT memory_id FROM memories
                WHERE memory_id = ?
                AND owner_id = ?
                AND workspace_id = ?
                """,
                (
                    memory.memory_id,
                    memory.owner_id,
                    memory.workspace_id,
                ),
            )
            if not cursor.fetchone():
                raise ValueError(
                    "Memory is unavailable in this scope"
                )
            
            # Cập nhật bản ghi
            cursor.execute(
                """
                UPDATE memories
                SET
                    memory_type = ?,
                    content = ?,
                    source = ?,
                    updated_at = ?,
                    confidence = ?,
                    sensitivity = ?,
                    expires_at = ?,
                    status = ?,
                    version = ?
                WHERE memory_id = ?
                AND owner_id = ?
                AND workspace_id = ?
                AND version = ?
                """,
                (
                    memory.memory_type.name,
                    memory.content,
                    memory.source,
                    memory.updated_at.isoformat(" "),
                    memory.confidence,
                    memory.sensitivity.name,
                    memory.expires_at.isoformat(" ") if memory.expires_at else None,
                    memory.status.name,
                    memory.version,
                    memory.memory_id,
                    memory.owner_id,
                    memory.workspace_id,
                    memory.version - 1,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Memory changed since it was read")
            self._write_keys(conn, memory.memory_id, memory.content)
            self._event(conn, memory.owner_id, memory.workspace_id, "update", memory.memory_id)
            
            # Trả về bản ghi đã cập nhật
            return self._get_by_id(
                conn,
                memory.memory_id,
                memory.owner_id,
                memory.workspace_id,
            )

    def soft_delete(
        self,
        memory_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> Optional[MemoryRecord]:
        """Xóa mềm một memory (đánh dấu là DELETED).
        
        Args:
            memory_id: ID của memory.
            owner_id: ID của chủ sở hữu.
            workspace_id: ID của workspace.
        
        Returns:
            Optional[MemoryRecord]: Bản ghi memory đã được xóa mềm nếu tồn tại, ngược lại None.
        """
        validate_identifier(owner_id)
        validate_identifier(workspace_id)
        with self._transaction() as conn:
            cursor = conn.cursor()
            
            # Cập nhật trạng thái thành DELETED
            cursor.execute(
                """
                UPDATE memories
                SET status = ?, updated_at = ?, version = version + 1
                WHERE memory_id = ?
                AND owner_id = ?
                AND workspace_id = ?
                AND status != 'DELETED'
                """,
                (
                    MemoryStatus.DELETED.name,
                    datetime.now(timezone.utc).isoformat(" "),
                    memory_id,
                    owner_id,
                    workspace_id,
                ),
            )
            if cursor.rowcount:
                self._event(conn, owner_id, workspace_id, "delete", memory_id)
            
            # Trả về bản ghi đã xóa mềm
            return self._get_by_id(conn, memory_id, owner_id, workspace_id)

    def supersede(self, memory_id: str, *, owner_id: str, workspace_id: str) -> bool:
        validate_identifier(owner_id)
        validate_identifier(workspace_id)
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE memories
                SET status = 'SUPERSEDED', updated_at = ?, version = version + 1
                WHERE memory_id = ? AND owner_id = ? AND workspace_id = ? AND status = 'ACTIVE'
                """,
                (datetime.now(timezone.utc).isoformat(" "), memory_id, owner_id, workspace_id),
            )
            if cursor.rowcount:
                self._event(conn, owner_id, workspace_id, "supersede", memory_id)
            return cursor.rowcount == 1

    def hard_delete(
        self,
        memory_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> bool:
        """Xóa cứng một memory khỏi cơ sở dữ liệu.
        
        Args:
            memory_id: ID của memory.
            owner_id: ID của chủ sở hữu.
            workspace_id: ID của workspace.
        
        Returns:
            bool: True nếu memory đã được xóa, ngược lại False.
        """
        validate_identifier(owner_id)
        validate_identifier(workspace_id)
        with self._transaction() as conn:
            cursor = conn.cursor()
            
            # Xóa bản ghi
            cursor.execute(
                """
                DELETE FROM memories
                WHERE memory_id = ?
                AND owner_id = ?
                AND workspace_id = ?
                """,
                (memory_id, owner_id, workspace_id),
            )
            
            if cursor.rowcount:
                self._event(conn, owner_id, workspace_id, "purge", memory_id)
            return cursor.rowcount > 0

    def search(
        self,
        owner_id: str,
        workspace_id: str,
        query: str,
        memory_types: Optional[Sequence[MemoryType]] = None,
        min_confidence: Optional[float] = None,
        limit: int = 5,
    ) -> list[MemoryRecord]:
        """Tìm kiếm memory theo truy vấn và các tiêu chí lọc.
        
        Args:
            owner_id: ID của chủ sở hữu.
            workspace_id: ID của workspace.
            query: Truy vấn tìm kiếm.
            memory_types: Danh sách loại memory để lọc (tùy chọn).
            min_confidence: Độ tin cậy tối thiểu (tùy chọn).
            limit: Số lượng kết quả tối đa.
        
        Returns:
            list[MemoryRecord]: Danh sách các memory phù hợp.
        """
        validate_identifier(owner_id)
        validate_identifier(workspace_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be positive")
        if min_confidence is not None and not 0 <= min_confidence <= 1:
            raise ValueError("min_confidence must be between 0 and 1")
        if len(query) > 10000:
            raise ValueError("Memory query exceeds the supported limit")
        # Literal tokens only; bound expression depth instead of leaking SQLite errors.
        tokens = list(dict.fromkeys(re.findall(r"\w+", query, flags=re.UNICODE)))
        if len(tokens) > 64:
            raise ValueError("Memory query has too many terms")
        if not tokens:
            return []
        literal_query = " AND ".join('"' + token + '"' for token in tokens)
        params: list[object] = [literal_query, owner_id, workspace_id]
        filters = ""
        if memory_types:
            filters += " AND m.memory_type IN (" + ",".join("?" for _ in memory_types) + ")"
            params.extend(t.name for t in memory_types)
        if min_confidence is not None:
            filters += " AND m.confidence >= ?"
            params.append(min_confidence)
        with self._transaction(write=False) as conn:
            # SQLite may otherwise choose the scope index as the outer loop and
            # rerun MATCH for every scoped record. Keep the FTS hit set outermost.
            matches = conn.execute(
                "SELECT m.memory_id,m.updated_at,m.confidence,m.memory_type, "
                "bm25(memories_fts) AS text_rank FROM memories_fts "
                "CROSS JOIN memories AS m ON m.rowid=memories_fts.rowid "
                "WHERE memories_fts MATCH ? AND m.owner_id=? AND m.workspace_id=? "
                "AND m.status='ACTIVE' AND (m.expires_at IS NULL OR julianday(m.expires_at)>julianday('now'))"
                + filters, params,
            ).fetchall()
            type_weights = {
                "SEMANTIC": 1.0, "PROCEDURAL": 0.95,
                "EPISODIC": 0.85, "TEMPORARY": 0.75,
            }
            # FTS5 BM25 is negative: more negative means more relevant.
            best_relevance = max((-float(row["text_rank"]) for row in matches), default=0)
            now = datetime.now(timezone.utc)
            def ranking_key(row):
                updated_at = datetime.fromisoformat(row["updated_at"])
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
                age_days = max((now - updated_at).total_seconds() / 86400, 0)
                recency = 1 / (1 + age_days)
                # Compress length/frequency differences so metadata still influences
                # near-equivalent matches; preserve BM25 order, including negatives.
                relevance = (-float(row["text_rank"]) / best_relevance) ** 0.5 if best_relevance > 0 else 0
                score = relevance * 0.55 + row["confidence"] * 0.25 + recency * 0.1 + type_weights[row["memory_type"]] * 0.1
                return -score, row["memory_id"]

            # Materialize only top-k, within the same snapshot as scoring.
            return [self._get_by_id(conn, row["memory_id"], owner_id, workspace_id)
                    for row in nsmallest(limit, matches, key=ranking_key)]

    def list_active(
        self,
        owner_id: str,
        workspace_id: Optional[str] = None,
        memory_types: Optional[Sequence[MemoryType]] = None,
        limit: Optional[int] = 20,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        """Liệt kê các memory đang hoạt động.
        
        Args:
            owner_id: ID của chủ sở hữu.
            workspace_id: ID của workspace.
            memory_types: Danh sách loại memory để lọc (tùy chọn).
            limit: Số lượng kết quả tối đa.
        
        Returns:
            list[MemoryRecord]: Danh sách các memory đang hoạt động.
        """
        validate_identifier(owner_id)
        if workspace_id is not None:
            validate_identifier(workspace_id)
        with self._transaction(write=False) as conn:
            cursor = conn.cursor()
            
            # Xây dựng truy vấn
            query = """
                SELECT * FROM memories
                WHERE status = 'ACTIVE'
                AND (expires_at IS NULL OR julianday(expires_at) > julianday('now'))
                AND owner_id = ?
            """
            params: list[object] = [owner_id]

            if workspace_id is not None:
                query += " AND workspace_id = ?"
                params.append(workspace_id)
            
            if memory_types:
                type_placeholders = ','.join(['?'] * len(memory_types))
                query += f" AND memory_type IN ({type_placeholders})"
                params.extend(t.name for t in memory_types)
            
            if offset < 0 or (limit is not None and limit < 1):
                raise ValueError("offset must be non-negative and limit must be positive")
            query += " ORDER BY updated_at DESC, memory_id"
            if limit is not None:
                query += " LIMIT ?"
                params.append(limit)
                if offset:
                    query += " OFFSET ?"
                    params.append(offset)
            elif offset:
                query += " LIMIT -1 OFFSET ?"
                params.append(offset)
            
            # Thực hiện truy vấn
            cursor.execute(query, params)
            
            return [self._row_to_memory_record(row) for row in cursor.fetchall()]

    def export(self, owner_id: str, workspace_id: str | None = None, *, limit: int | None = None, offset: int = 0) -> list[MemoryRecord]:
        validate_identifier(owner_id)
        if workspace_id is not None:
            validate_identifier(workspace_id)
        if offset < 0 or (limit is not None and limit < 1):
            raise ValueError("Invalid export pagination")
        query = "SELECT * FROM memories WHERE owner_id=?"
        params: list[object] = [owner_id]
        if workspace_id is not None:
            query += " AND workspace_id=?"
            params.append(workspace_id)
        query += " ORDER BY created_at, memory_id LIMIT ? OFFSET ?"
        params.extend((-1 if limit is None else limit, offset))
        with self._transaction(write=False) as conn:
            return [self._row_to_memory_record(row) for row in conn.execute(query, params)]

    def forget(self, owner_id: str, workspace_id: str | None = None) -> int:
        validate_identifier(owner_id)
        if workspace_id is not None:
            validate_identifier(workspace_id)
        query = "DELETE FROM memories WHERE owner_id=?"
        params = [owner_id]
        if workspace_id is not None:
            query += " AND workspace_id=?"
            params.append(workspace_id)
        with self._transaction() as conn:
            count = conn.execute(query, params).rowcount
            self._event(conn, owner_id, workspace_id, "purge", count=count)
            return count

    def auto_memory_enabled(self, owner_id: str, workspace_id: str, *, default: bool = False) -> bool:
        validate_identifier(owner_id)
        validate_identifier(workspace_id)
        with self._transaction(write=False) as conn:
            row = conn.execute("SELECT auto_memory_enabled FROM memory_preferences WHERE owner_id=? AND workspace_id=?", (owner_id, workspace_id)).fetchone()
            return bool(row[0]) if row else default

    def set_auto_memory(self, owner_id: str, workspace_id: str, enabled: bool) -> None:
        validate_identifier(owner_id)
        validate_identifier(workspace_id)
        if type(enabled) is not bool:
            raise ValueError("Auto-memory preference must be a boolean")
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO memory_preferences VALUES (?,?,?,?) ON CONFLICT(owner_id,workspace_id) "
                "DO UPDATE SET auto_memory_enabled=excluded.auto_memory_enabled, updated_at=excluded.updated_at",
                (owner_id, workspace_id, int(enabled), datetime.now(timezone.utc).isoformat(" ")),
            )
            self._event(conn, owner_id, workspace_id, "preference")

    def _row_to_memory_record(self, row: sqlite3.Row) -> MemoryRecord:
        """Chuyển đổi một hàng từ cơ sở dữ liệu thành MemoryRecord.
        
        Args:
            row: Hàng từ cơ sở dữ liệu.
        
        Returns:
            MemoryRecord: Bản ghi memory đã được chuyển đổi.
        """
        return MemoryRecord(
            memory_id=row['memory_id'],
            owner_id=row['owner_id'],
            workspace_id=row['workspace_id'],
            memory_type=MemoryType[row['memory_type']],
            content=row['content'],
            source=row['source'],
            created_at=datetime.fromisoformat(row['created_at']),
            updated_at=datetime.fromisoformat(row['updated_at']),
            confidence=row['confidence'],
            sensitivity=MemorySensitivity[row['sensitivity']],
            expires_at=(datetime.fromisoformat(row['expires_at']) if row['expires_at'] else None),
            status=MemoryStatus[row['status']],
            version=row['version'],
        )

__all__ = [
    "MemoryRepository",
]
