-- Persist user controls and content-free mutation audit for the Memory Engine.
CREATE TABLE memory_preferences (
    owner_id TEXT NOT NULL CHECK(length(trim(owner_id)) > 0),
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) > 0),
    auto_memory_enabled INTEGER NOT NULL CHECK(auto_memory_enabled IN (0,1)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (owner_id, workspace_id)
);
CREATE TABLE memory_events (
    event_id INTEGER PRIMARY KEY,
    owner_id TEXT NOT NULL CHECK(length(trim(owner_id)) > 0),
    workspace_id TEXT,
    action TEXT NOT NULL CHECK(action IN ('create','update','delete','supersede','purge','preference')),
    memory_id TEXT,
    affected_count INTEGER NOT NULL CHECK(affected_count >= 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
