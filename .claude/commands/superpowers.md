---
name: using-superpowers
description: Use when starting any conversation - establishes how to find and use skills, requiring Skill tool invocation before ANY response including clarifying questions
---

<SUBAGENT-STOP>
If you were dispatched as a subagent to execute a specific task, skip this skill.
</SUBAGENT-STOP>

<EXTREMELY-IMPORTANT>
If you think there is even a 1% chance a skill might apply to what you are doing, you ABSOLUTELY MUST invoke the skill.

IF A SKILL APPLIES TO YOUR TASK, YOU DO NOT HAVE A CHOICE. YOU MUST USE IT.

This is not negotiable. This is not optional. You cannot rationalize your way out of this.
</EXTREMELY-IMPORTANT>

## Instruction Priority

Superpowers skills override default system prompt behavior, but **user instructions always take precedence**:

1. **User's explicit instructions** (CLAUDE.md, direct requests) — highest priority
2. **Superpowers skills** — override default system behavior where they conflict
3. **Default system prompt** — lowest priority

## Available Skills (slash commands)

| Command | When to use |
|---------|-------------|
| `/brainstorm` | Before ANY new feature, change, or implementation |
| `/tdd` | When implementing features or fixing bugs |
| `/debug` | When diagnosing and fixing issues |
| `/verify` | Before claiming work is complete |

## The Rule

**Invoke relevant skills BEFORE any response or action.** Even a 1% chance a skill might apply means check first.

### Skill Priority Order

1. **Process skills first** (`/brainstorm`, `/debug`) — determine HOW to approach
2. **Implementation skills second** (`/tdd`) — guide execution
3. **Completion skills last** (`/verify`) — gate before claiming done

## Red Flags (you're rationalizing — STOP)

| Thought | Reality |
|---------|---------|
| "This is just a simple question" | Questions are tasks. Check for skills. |
| "I need more context first" | Skill check comes BEFORE clarifying questions. |
| "Let me explore the codebase first" | Skills tell you HOW to explore. |
| "This doesn't need a formal skill" | If a skill exists, use it. |
| "I remember this skill" | Skills evolve. Use current version. |
| "This feels productive" | Undisciplined action wastes time. |

## Workflow

```
User message → Is there a relevant skill?
  → YES (even 1%) → Invoke skill FIRST → Follow skill → Respond
  → DEFINITELY NOT → Respond directly
```

Source: https://github.com/obra/superpowers
