---
name: brainstorming
description: Use BEFORE any new feature, component, or behavior change - design first, code never comes first
---

# Brainstorming: Design Before Code

## Overview

**Do NOT write any code, scaffold any project, or take any implementation action until you have presented a design and the user has approved it.**

Simple projects are where unexamined assumptions cause the most wasted work.

## The Iron Law

```
NO IMPLEMENTATION WITHOUT APPROVED DESIGN
```

Even a single-function utility requires a design doc (though it can be brief).

## The Process (9 Steps)

### Step 1: Explore Project Context

Before asking anything:
- Review relevant files and recent commits
- Understand current architecture
- Identify constraints and dependencies

### Step 2: Offer Visual Companion (if applicable)

If the topic involves UI, data flow, or architecture — offer to create a diagram.

### Step 3: Ask Clarifying Questions

**One at a time.** Prefer multiple-choice over open-ended:

```
What's the priority for this feature?
A) Speed (ship fast, polish later)
B) Quality (comprehensive tests, full edge cases)
C) Minimal (smallest possible change)
```

Don't ask all questions at once. Wait for each answer before proceeding.

### Step 4: Propose 2-3 Approaches

For each approach, cover:
- What it does
- Trade-offs (pros/cons)
- Complexity estimate
- Recommendation with reasoning

### Step 5: Present Design in Sections

For larger features, present sections and get approval before proceeding to the next. Don't dump the entire design at once.

### Step 6: Write Design Document

Save to: `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`

Include:
- Goal and success criteria
- Chosen approach and rationale
- Architecture overview
- Key decisions and trade-offs
- Out of scope (explicit)

### Step 7: Self-Review

Check the spec for:
- Placeholders or "TBD" sections
- Contradictions between sections
- Ambiguous requirements
- Missing edge cases

### Step 8: User Reviews Written Spec

Ask the user to review and approve the written spec before proceeding.

### Step 9: Create Implementation Plan

After approval, invoke `/write-plan` (or proceed to plan creation).

The ONLY next step after brainstorming is planning — not coding.

## Red Flags - STOP

- Writing any code before step 9
- Skipping design doc for "simple" changes
- Asking multiple questions simultaneously
- Moving to implementation without explicit approval

## Questions to Ask Yourself

Before each response:
- "Am I about to write code without an approved design?"
- "Have I asked all necessary clarifying questions?"
- "Is the design doc complete and approved?"

If any answer is "no" — don't write code.

Source: https://github.com/obra/superpowers
