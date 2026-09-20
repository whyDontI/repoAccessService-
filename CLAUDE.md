# Working preferences for this project

## Writing style (README.md, any docs)

- Plain human English. No corporate/AI-sounding text walls.
- Short bullet points over paragraphs. One idea per line.
- Bold/headers for structure, not filler.
- If a section reads like a wall of text, break it up.

## How we build

- Step by step, one reviewable chunk at a time. Never one-shot the whole thing.
- Implement a piece, show it, get a go-ahead, move to the next.
- Keep plan.md updated as we go — what's planned vs what's built.
- Log tradeoffs and decisions into README.md as we make them, not at the end.

## Rules from the assignment brief (still apply)

- No abstraction (interface, factory, plugin system, config layer) used only once —
  ask first if one seems needed.
- Direct implementation over flexible/generic.
- No new dependency unless explicitly asked. Standard library first.
- Every file readable start to end in under 2 minutes.
- Never touch a file not asked about.
- After writing code, briefly explain what each function does and why.
- If a requirement is ambiguous, decide and document it (this is graded), don't
  silently guess — but don't block on things that aren't actually load-bearing either.

## Design decisions locked for this project

See README.md's "Requirements and tradeoffs" section — that's the source of truth,
not this file. Key ones:

- Cache the user's team set, never cache grants — checks read grants live.
- Org grants cover their repos, but a repo-level grant can be higher — take the max.
- Explain never uses the cache, always walks fresh.
- Team hierarchy treated as a graph, not a tree (cycle-safe everywhere).
- Fixture generator deliberately plants cycles and edge cases.
- No Redis, single in-process cache, one backend instance.
