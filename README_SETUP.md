# Install into an existing TripWise repository

This is a **starter overlay**, NOT a runnable TripWise application. It has not been tested against your team's repository.

1. Make a branch (`git switch -c feat/ai-agent-workflow`) from a clean working tree, coordinate with teammates.
2. Unzip and **copy the contents** of this folder into the existing TripWise root (not as a nested TripWise_Codex_Starter directory). Review any collision with an existing AGENTS.md before merging.
3. In VS Code, open the real repository root. Restart/reload Codex if the skills/agents are not discovered.
4. Check `.agents/skills/*/SKILL.md` and `.codex/agents/*.toml` are visible. Skills are workflows; TOML entries are optional custom subagents (depending on Codex version). Neither file creation nor a roleplay statement proves an agent ran.
5. Use tasks **sequentially** (one new Codex task per role):
   - `Use $analyst, read docs/ai/brief.md and prepare docs/ai/spec.md. Separate assumptions. Do not edit code.`
   - Review and approve spec yourself.
   - `Use $designer, read docs/ai/spec.md and prepare docs/ai/ui.md. Do not edit code.`
   - `Use $developer, implement only the first POI validation/ranking slice with tests. Run tests and report results.`
   - `Use $qa, evaluate against spec and UI contract, write docs/ai/review.md. Do not edit code.`
   - Send reproducible issue to developer, then QA retest.
6. To test *actual subagents* where available, ask parent Codex to delegate the spec analysis to project subagent `analyst`, inspect separate task/thread output and artifact. If unavailable use separate tasks with explicit skill; this satisfies the course's demonstrated sequence.
7. Keep evidence of actual skill file loading, independent role runs, real MCP tool call and its result, user interviews and usability tests. This starter intentionally does **not** fake any of them.

## Course vs Codex directory paths
The MAI HTML instructions illustrate `.github/skills/` for VS Code Chat. Codex (the tool you use) recognizes repository skills in `.agents/skills/`; these are different clients and paths. If you use VS Code built-in Chat instead of Codex, adapt the path rather than making duplicate conflicting skills.

## Reminder
The university rubric gives points for interview conclusions, reproducible use, real skill-and-role handoff, third-party MCP usage, presentation and defense. A configuration archive alone gives no evidence. The coursework allows 1–3 team members; verify grading scope if the full TripWise team exceeds this number.
