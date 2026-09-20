-- Subjects: users and teams. A team can nest inside another team.
CREATE TABLE subjects (
    id BIGSERIAL PRIMARY KEY,
    type TEXT NOT NULL CHECK (type IN ('user', 'team')),
    name TEXT NOT NULL
);

-- Resources: orgs and repos. A repo belongs to an org via org_id.
CREATE TABLE resources (
    id BIGSERIAL PRIMARY KEY,
    type TEXT NOT NULL CHECK (type IN ('org', 'repo')),
    org_id BIGINT REFERENCES resources(id),
    name TEXT NOT NULL
);

-- One row per membership edge: subject_id belongs to belongs_to_team_id.
-- belongs_to_team_id is NULL for a top-level team.
-- Not a strict tree: a subject can have more than one row here (multiple parents),
-- and the data may contain cycles on purpose (see fixture generator) — every reader
-- of this table must carry a visited-set.
CREATE TABLE team_relationships (
    id BIGSERIAL PRIMARY KEY,
    subject_id BIGINT NOT NULL REFERENCES subjects(id),
    belongs_to_team_id BIGINT REFERENCES subjects(id)
);

-- A grant of a role to a subject on a resource. Role ordering (read < write <
-- admin < owner) is handled in application code, not here.
CREATE TABLE grants (
    id BIGSERIAL PRIMARY KEY,
    subject_id BIGINT NOT NULL REFERENCES subjects(id),
    role TEXT NOT NULL CHECK (role IN ('read', 'write', 'admin', 'owner')),
    resource_id BIGINT NOT NULL REFERENCES resources(id)
);

-- checker.py walks team_relationships from a user upward (subject_id -> parent).
CREATE INDEX idx_team_relationships_subject ON team_relationships(subject_id);
-- needed for invalidate_all's trigger case: finding a team's own parent edge when it's re-parented.
CREATE INDEX idx_team_relationships_parent ON team_relationships(belongs_to_team_id);

-- checker.py's live grants lookup: subject_id IN (...) AND resource_id IN (repo, org).
CREATE INDEX idx_grants_subject_resource ON grants(subject_id, resource_id);
-- for "show all grants on this resource" (browse UI).
CREATE INDEX idx_grants_resource ON grants(resource_id);
