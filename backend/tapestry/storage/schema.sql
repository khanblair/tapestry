-- Tapestry storage schema (v1: single-user, local-first, no multi-tenancy/auth).
--
-- conversations: what an event belongs to. kind is "dm" or "group" per the
-- scoped spec's persona model (one-on-one vs. group conversations).
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('dm', 'group')),
    name TEXT,
    created_at TEXT NOT NULL
);

-- events: the append-only event log -- the actual source of truth per
-- project_structure.md's core/events.py. Everything else (conversations.py's
-- Message/Conversation projections, the skills catalog, etc.) is derived by
-- reading this table, never by mutating it.
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    actor TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

-- The event log is always read scoped to one conversation (a projection
-- rebuild, a catalog lookup, etc.), so this is the one index that matters.
CREATE INDEX IF NOT EXISTS idx_events_conversation_id ON events(conversation_id);
