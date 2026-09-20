// Types are generated, not hand-written -- see generated-api-types.ts's
// own header and README for the regen command. That file is produced
// from backend/openapi.json, which is itself produced straight from the
// Pydantic models (backend/scripts/export_openapi.py), so these types
// can't silently drift from what the backend actually serves: a changed
// field only shows up here after someone deliberately regenerates.
import type { components } from "./generated-api-types";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export type Role = components["schemas"]["Role"];
export type CheckResponse = components["schemas"]["CheckResponse"];
export type ExplainResponse = components["schemas"]["ExplainResponse"];
export type Org = components["schemas"]["OrgOut"];
export type Repo = components["schemas"]["RepoOut"];
export type Resource = components["schemas"]["ResourceOut"];
export type Grant = components["schemas"]["GrantOut"];
export type SubjectRef = components["schemas"]["SubjectRef"];
export type Team = components["schemas"]["TeamOut"];

// Role comes back over the wire as its underlying int (1-4), since
// that's how pydantic serializes an IntEnum by default -- mapped back
// to a label here rather than changing the wire format.
export const ROLE_LABELS: Record<Role, string> = {
  1: "read",
  2: "write",
  3: "admin",
  4: "owner",
};
export const ACTIONS = ["read", "write", "admin", "owner"] as const;
export type Action = (typeof ACTIONS)[number];

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
