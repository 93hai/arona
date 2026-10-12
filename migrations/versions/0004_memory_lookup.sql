-- Derived keys for active Memory only. NULL means pending canonicalization.
-- Plain SQLite triggers preserve invalidation even for maintenance SQL writes.
-- Unicode canonicalization remains in Python, shared with admission semantics.
CREATE TABLE memory_lookup (
    memory_id TEXT PRIMARY KEY NOT NULL REFERENCES memories(memory_id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    normalized_content TEXT,
    subject_key TEXT,
    CHECK ((normalized_content IS NULL) = (subject_key IS NULL))
);
CREATE INDEX idx_memory_lookup_pending ON memory_lookup(owner_id, workspace_id)
    WHERE normalized_content IS NULL;
CREATE INDEX idx_memory_lookup_duplicate
    ON memory_lookup(owner_id, workspace_id, memory_type, normalized_content);
CREATE INDEX idx_memory_lookup_subject
    ON memory_lookup(owner_id, workspace_id, subject_key);
INSERT INTO memory_lookup(memory_id, owner_id, workspace_id, memory_type)
    SELECT memory_id, owner_id, workspace_id, memory_type FROM memories WHERE status='ACTIVE';

CREATE TRIGGER memory_lookup_ai AFTER INSERT ON memories WHEN new.status='ACTIVE' BEGIN
    INSERT INTO memory_lookup(memory_id, owner_id, workspace_id, memory_type)
        VALUES (new.memory_id, new.owner_id, new.workspace_id, new.memory_type);
END;
CREATE TRIGGER memory_lookup_au AFTER UPDATE ON memories
WHEN old.memory_id IS NOT new.memory_id OR old.owner_id IS NOT new.owner_id
    OR old.workspace_id IS NOT new.workspace_id OR old.memory_type IS NOT new.memory_type
    OR old.content IS NOT new.content OR old.status IS NOT new.status
BEGIN
    DELETE FROM memory_lookup WHERE memory_id=old.memory_id;
    INSERT INTO memory_lookup(memory_id, owner_id, workspace_id, memory_type)
        SELECT new.memory_id, new.owner_id, new.workspace_id, new.memory_type
        WHERE new.status='ACTIVE';
END;
CREATE TRIGGER memory_lookup_ad AFTER DELETE ON memories BEGIN
    DELETE FROM memory_lookup WHERE memory_id=old.memory_id;
END;
