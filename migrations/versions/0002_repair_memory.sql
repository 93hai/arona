-- Repair the candidate v1 schema without discarding any Memory records.
DROP TRIGGER IF EXISTS memories_ai;
DROP TRIGGER IF EXISTS memories_au;
DROP TRIGGER IF EXISTS memories_ad;
DROP TABLE IF EXISTS memories_fts;

CREATE TABLE memories_v2 (
    memory_id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(memory_id)) > 0),
    owner_id TEXT NOT NULL CHECK(length(trim(owner_id)) > 0),
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) > 0),
    memory_type TEXT NOT NULL CHECK(memory_type IN ('SEMANTIC','EPISODIC','PROCEDURAL','TEMPORARY')),
    content TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    sensitivity TEXT NOT NULL CHECK(sensitivity IN ('PUBLIC','INTERNAL','SENSITIVE','SECRET')),
    expires_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE','SUPERSEDED','DELETED')),
    version INTEGER NOT NULL CHECK(version >= 1)
);
INSERT INTO memories_v2
SELECT memory_id,owner_id,workspace_id,memory_type,content,source,created_at,
       updated_at,confidence,sensitivity,expires_at,status,version
FROM memories;
DROP TABLE memories;
ALTER TABLE memories_v2 RENAME TO memories;
CREATE INDEX idx_memories_scope ON memories(owner_id,workspace_id,status,expires_at);

-- Normal contentful FTS5: rowid is SQLite's integer rowid, NOT memory_id.
CREATE VIRTUAL TABLE memories_fts USING fts5(
    content,source,memory_id UNINDEXED,owner_id UNINDEXED,workspace_id UNINDEXED,
    memory_type UNINDEXED,confidence UNINDEXED,sensitivity UNINDEXED,
    expires_at UNINDEXED,status UNINDEXED, tokenize='unicode61 remove_diacritics 2'
);
INSERT INTO memories_fts
    (rowid,content,source,memory_id,owner_id,workspace_id,memory_type,
     confidence,sensitivity,expires_at,status)
SELECT rowid,content,source,memory_id,owner_id,workspace_id,memory_type,
       confidence,sensitivity,expires_at,status FROM memories;

CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts
        (rowid,content,source,memory_id,owner_id,workspace_id,memory_type,
         confidence,sensitivity,expires_at,status)
    VALUES (new.rowid,new.content,new.source,new.memory_id,new.owner_id,new.workspace_id,
            new.memory_type,new.confidence,new.sensitivity,new.expires_at,new.status);
END;
CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
    DELETE FROM memories_fts WHERE rowid=old.rowid;
    INSERT INTO memories_fts
        (rowid,content,source,memory_id,owner_id,workspace_id,memory_type,
         confidence,sensitivity,expires_at,status)
    VALUES (new.rowid,new.content,new.source,new.memory_id,new.owner_id,new.workspace_id,
            new.memory_type,new.confidence,new.sensitivity,new.expires_at,new.status);
END;
CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
    DELETE FROM memories_fts WHERE rowid=old.rowid;
END;
CREATE TABLE maintenance_events (
    event_id INTEGER PRIMARY KEY,
    actor_id TEXT NOT NULL,
    scope TEXT NOT NULL CHECK(scope='database'),
    action TEXT NOT NULL,
    from_version INTEGER NOT NULL,
    to_version INTEGER NOT NULL,
    backup_name TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
