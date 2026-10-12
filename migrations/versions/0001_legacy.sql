-- Tạo bảng memories
CREATE TABLE IF NOT EXISTS memories (
    memory_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    confidence REAL NOT NULL,
    sensitivity TEXT NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL,
    FOREIGN KEY (owner_id) REFERENCES owners(owner_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id)
);

-- Tạo index cho các cột thường xuyên truy vấn
CREATE INDEX IF NOT EXISTS idx_memories_owner_id ON memories(owner_id);
CREATE INDEX IF NOT EXISTS idx_memories_workspace_id ON memories(workspace_id);
CREATE INDEX IF NOT EXISTS idx_memories_memory_type ON memories(memory_type);
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);
CREATE INDEX IF NOT EXISTS idx_memories_expires_at ON memories(expires_at);

-- Tạo bảng FTS5 cho tìm kiếm toàn văn
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    source,
    memory_id UNINDEXED,
    owner_id UNINDEXED,
    workspace_id UNINDEXED,
    memory_type UNINDEXED,
    confidence UNINDEXED,
    sensitivity UNINDEXED,
    expires_at UNINDEXED,
    status UNINDEXED
);

-- Tạo trigger để đồng bộ dữ liệu giữa memories và memories_fts
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories
BEGIN
    INSERT INTO memories_fts(
        rowid, content, source, memory_id, owner_id, workspace_id,
        memory_type, confidence, sensitivity, expires_at, status
    )
    VALUES (
        new.memory_id, new.content, new.source, new.memory_id, new.owner_id,
        new.workspace_id, new.memory_type, new.confidence, new.sensitivity,
        new.expires_at, new.status
    );
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories
BEGIN
    INSERT INTO memories_fts(
        memories_fts, rowid, content, source, memory_id, owner_id, workspace_id,
        memory_type, confidence, sensitivity, expires_at, status
    )
    VALUES (
        'delete', old.memory_id, old.content, old.source, old.memory_id, old.owner_id,
        old.workspace_id, old.memory_type, old.confidence, old.sensitivity,
        old.expires_at, old.status
    );
    INSERT INTO memories_fts(
        rowid, content, source, memory_id, owner_id, workspace_id,
        memory_type, confidence, sensitivity, expires_at, status
    )
    VALUES (
        new.memory_id, new.content, new.source, new.memory_id, new.owner_id,
        new.workspace_id, new.memory_type, new.confidence, new.sensitivity,
        new.expires_at, new.status
    );
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories
BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, source)
    VALUES ('delete', old.memory_id, old.content, old.source);
END;
