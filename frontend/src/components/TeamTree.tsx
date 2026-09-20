import type { Team } from "../api";

// Renders a team's immediate neighborhood as a tree: its parent above
// (if any), the team itself, and its children below. Only one level
// each way -- the API doesn't return more than that in one call -- but
// that's enough to see where a team sits in the hierarchy.
export default function TeamTree({ team }: { team: Team }) {
  return (
    <ul className="tree">
      {team.parent ? (
        <li>
          <TreeNode name={team.parent.name} type={team.parent.type} />
          <ul>
            <li>
              <TreeNode name={team.name} type="team" current />
              <Children team={team} />
            </li>
          </ul>
        </li>
      ) : (
        <li>
          <TreeNode name={team.name} type="team" current />
          <Children team={team} />
        </li>
      )}
    </ul>
  );
}

function Children({ team }: { team: Team }) {
  if (team.children.length === 0) return null;
  return (
    <ul>
      {team.children.map((child) => (
        <li key={child.id}>
          <TreeNode name={child.name} type={child.type} />
        </li>
      ))}
    </ul>
  );
}

function TreeNode({ name, type, current }: { name: string; type: string; current?: boolean }) {
  return (
    <span className={`tree-node${current ? " current" : ""}`}>
      {name} <span className="type-badge">{type}</span>
    </span>
  );
}
