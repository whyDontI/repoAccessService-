import { useState } from "react";
import { ACTIONS, checkAccess, explainAccess, type Action, type CheckResponse, type ExplainResponse } from "../api";
import ExplainView from "./ExplainView";

// No endpoint exists to search 50k users by name (out of scope for this
// pass -- see README), so this is a plain numeric id for now.
export default function CheckPanel() {
  const [userId, setUserId] = useState("");
  const [repoId, setRepoId] = useState("");
  const [action, setAction] = useState<Action>("read");
  const [check, setCheck] = useState<CheckResponse | null>(null);
  const [explain, setExplain] = useState<ExplainResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleCheck() {
    setError(null);
    setLoading(true);
    try {
      const [checkResult, explainResult] = await Promise.all([
        checkAccess(Number(userId), action, Number(repoId)),
        explainAccess(Number(userId), action, Number(repoId)),
      ]);
      setCheck(checkResult);
      setExplain(explainResult);
    } catch (e) {
      setError(String(e));
      setCheck(null);
      setExplain(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section>
      <h2>Check access</h2>
      <div className="field-row">
        <label>
          User ID
          <input value={userId} onChange={(e) => setUserId(e.target.value)} placeholder="e.g. 1316" />
        </label>
        <label>
          Repo ID
          <input value={repoId} onChange={(e) => setRepoId(e.target.value)} placeholder="e.g. 100" />
        </label>
        <label>
          Action
          <select value={action} onChange={(e) => setAction(e.target.value as Action)}>
            {ACTIONS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </label>
        <button onClick={handleCheck} disabled={!userId || !repoId || loading}>
          {loading ? "Checking..." : "Check"}
        </button>
      </div>

      {error && <p className="error">{error}</p>}

      {check && (
        <div className={`result ${check.allowed ? "allowed" : "denied"}`}>
          <strong>{check.allowed ? "Allowed" : "Denied"}</strong>
          {check.as_of && (
            <p className="staleness-note">
              Based on team membership cached as of {new Date(check.as_of).toLocaleTimeString()}. Grant
              data itself is always read live, so a revoked grant shows up on the very next check
              regardless.
            </p>
          )}
        </div>
      )}

      {explain && <ExplainView explain={explain} />}
    </section>
  );
}
