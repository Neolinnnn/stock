---
name: systematic-debugging
description: Use when diagnosing and fixing bugs or unexpected behavior
---

# Systematic Debugging

## Overview

Random fixing wastes 2-3 hours and has 40% success rate.
Systematic debugging takes 15-30 minutes and has 95% first-time fix rate.

**Core principle:** Root cause investigation MUST precede any fix attempt.

## The Iron Law

```
NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST
```

## Four Mandatory Phases

### Phase 1: Root Cause Investigation

**Before touching any code:**

1. Read the full error message carefully
2. Reproduce the issue consistently
3. Check recent changes (git log, git diff)
4. Gather diagnostic evidence at component boundaries:
   - Log what data enters each component
   - Log what data exits each component
   - Trace data flow backward to find the source

```python
# Example: Add temporary diagnostic logging
print(f"[DEBUG] Input to component: {input_data}")
result = component.process(input_data)
print(f"[DEBUG] Output from component: {result}")
```

### Phase 2: Pattern Analysis

1. Find working examples (similar code that works)
2. Compare implementations thoroughly
3. Identify ALL differences between working and broken
4. Understand dependencies

### Phase 3: Hypothesis and Testing

1. Form a specific, testable hypothesis
2. Test with minimal changes (one variable at a time)
3. Never try multiple fixes simultaneously
4. Verify results before proceeding

### Phase 4: Implementation

1. Write a failing test that reproduces the bug
2. Implement ONE fix targeting the root cause
3. Verify the fix works
4. Don't bundle unrelated changes

## Red Flags - STOP

These indicate you've abandoned systematic debugging:

- Proposing a fix without completing Phase 1
- Trying multiple simultaneous changes
- Skipping the failing test step
- Saying "let me just try X and see"
- Continuing after 3 failed fix attempts without questioning architecture

## After 3 Failed Attempts

If three fixes have failed, **STOP**. Don't attempt a 4th.

Instead:
1. Step back and question the architecture
2. Ask: "Is the problem somewhere fundamentally different?"
3. Discuss with your human partner before proceeding

## Verification Checklist

- [ ] Reproduced the bug consistently
- [ ] Identified root cause (not just symptoms)
- [ ] Checked recent changes that might have caused it
- [ ] Written a failing test that reproduces the issue
- [ ] Implemented single targeted fix
- [ ] Verified fix passes the test
- [ ] Verified no regressions

## The Bottom Line

Symptoms lie. Fix root causes.

Source: https://github.com/obra/superpowers
