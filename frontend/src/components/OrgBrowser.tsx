import { useEffect, useState } from "react";
import {
  ROLE_LABELS,
  getResourceGrants,
  getTeam,
  listOrgRepos,
  listOrgs,
  type Grant,
  type Org,
  type Repo,
  type Team,
} from "../api";
import TeamTree from "./TeamTree";

export default function OrgBrowser() {
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [selectedOrgId, setSelectedOrgId] = useState<number | null>(null);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [selectedRepoId, setSelectedRepoId] = useState<number | null>(null);
  const [grants, setGrants] = useState<Grant[]>([]);

  const [teamIdInput, setTeamIdInput] = useState("");
  const [team, setTeam] = useState<Team | null>(null);
  const [teamError, setTeamError] = useState<string | null>(null);

  useEffect(() => {
    listOrgs().then(setOrgs).catch(() => setOrgs([]));
  }, []);

  useEffect(() => {
    if (selectedOrgId === null) return;
    setSelectedRepoId(null);
    setGrants([]);
    listOrgRepos(selectedOrgId).then(setRepos).catch(() => setRepos([]));
  }, [selectedOrgId]);

  useEffect(() => {
    if (selectedRepoId === null) return;
    getResourceGrants(selectedRepoId).then(setGrants).catch(() => setGrants([]));
  }, [selectedRepoId]);

  async function lookupTeam() {
    setTeamError(null);
    try {
      setTeam(await getTeam(Number(teamIdInput)));
    } catch (e) {
      setTeam(null);
      setTeamError(String(e));
    }
  }

  return (
    <section>
      <h2>Browse</h2>

      <div className="field-row">
        <label>
          Org
          <select
            value={selectedOrgId ?? ""}
            onChange={(e) => setSelectedOrgId(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">Select an org...</option>
            {orgs.map((org) => (
              <option key={org.id} value={org.id}>
                {org.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      {selectedOrgId !== null && (
        <ul className="repo-list">
          {repos.map((repo) => (
            <li key={repo.id}>
              <button className="link" onClick={() => setSelectedRepoId(repo.id)}>
                {repo.name}
              </button>
            </li>
          ))}
        </ul>
      )}

      {selectedRepoId !== null && (
        <div>
          <h3>Grants on this repo</h3>
          {grants.length === 0 ? (
            <p>No grants directly on this repo (access may still come from its org).</p>
          ) : (
            <ul>
              {grants.map((grant) => (
                <li key={grant.id}>
                  {grant.subject_name} <span className="type-badge">{grant.subject_type}</span>:{" "}
                  {ROLE_LABELS[grant.role]}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <h3>Team lookup</h3>
      <div className="field-row">
        <input
          value={teamIdInput}
          onChange={(e) => setTeamIdInput(e.target.value)}
          placeholder="team id, e.g. 50008"
        />
        <button onClick={lookupTeam} disabled={!teamIdInput}>
          Look up
        </button>
      </div>
      {teamError && <p className="error">{teamError}</p>}
      {team && <TeamTree team={team} />}
    </section>
  );
}
