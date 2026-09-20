// Hand-written to match backend/app/models.py -- FastAPI does expose an
// OpenAPI schema this could be generated from instead, but for a handful
// of endpoints this is easier to keep in sync by eye than to wire up a
// codegen step for.

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// Role comes back over the wire as its underlying int (1-4), since
// that's how pydantic serializes an IntEnum by default -- mapped back
// to a label here rather than changing the wire format.
export const ROLE_LABELS: Record<number, string> = {
  1: "read",
  2: "write",
  3: "admin",
  4: "owner",
};
export const ACTIONS = ["read", "write", "admin", "owner"] as const;
export type Action = (typeof ACTIONS)[number];

export interface CheckResponse {
  allowed: boolean;
  as_of: string | null;
}

export interface ExplainResponse {
  allowed: boolean;
  chain: string[];
  granted_role: number | null;
  reason: string | null;
}

export interface Org {
  id: number;
  name: string;
}

export interface Repo {
  id: number;
  name: string;
  org_id: number;
}

export interface Resource {
  id: number;
  name: string;
  type: "org" | "repo";
  org_id: number | null;
}

export interface Grant {
  id: number;
  subject_id: number;
  subject_name: string;
  subject_type: "user" | "team";
  role: number;
  resource_id: number;
  resource_name: string;
  resource_type: "org" | "repo";
}

export interface SubjectRef {
  id: number;
  name: string;
  type: "user" | "team";
}

export interface Team {
  id: number;
  name: string;
  parent: SubjectRef | null;
  children: SubjectRef[];
}

async function getJson<T>(path: string): Promise<T> {
  const resp = await fetch(`${API_URL}${path}`, {
    // the API's answer can change the moment a mutation lands, so the
    // browser must never serve a cached copy of a stale /check result
    cache: "no-store",
  });
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`${resp.status} ${resp.statusText}: ${body}`);
  }
  return resp.json();
}

export function checkAccess(userId: number, action: Action, repoId: number) {
  return getJson<CheckResponse>(`/check?user_id=${userId}&action=${action}&repo_id=${repoId}`);
}

export function explainAccess(userId: number, action: Action, repoId: number) {
  return getJson<ExplainResponse>(`/explain?user_id=${userId}&action=${action}&repo_id=${repoId}`);
}

export function listOrgs() {
  return getJson<Org[]>("/orgs");
}

export function listOrgRepos(orgId: number) {
  return getJson<Repo[]>(`/orgs/${orgId}/repos`);
}

export function getResource(resourceId: number) {
  return getJson<Resource>(`/resource/${resourceId}`);
}

export function getResourceGrants(resourceId: number) {
  return getJson<Grant[]>(`/resource/${resourceId}/grants`);
}

export function getTeam(teamId: number) {
  return getJson<Team>(`/teams/${teamId}`);
}
