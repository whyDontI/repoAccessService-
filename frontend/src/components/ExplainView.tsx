import type { ExplainResponse } from "../api";

// Renders the chain from /explain as a readable breadcrumb, for both
// allows and denies -- a non-engineer should be able to follow this
// without knowing what a "team" or "grant" means internally.
export default function ExplainView({ explain }: { explain: ExplainResponse }) {
  return (
    <div className="explain">
      <h3>Why?</h3>
      {explain.chain.length > 0 ? (
        <p className="chain">{explain.chain.join(" → ")}</p>
      ) : (
        <p className="chain">No connection found to this resource at all.</p>
      )}
      {explain.reason && <p className="reason">{explain.reason}</p>}
    </div>
  );
}
