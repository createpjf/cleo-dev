# Team Roster

_Auto-generated from agents.yaml on 2026-02-20 14:00_

Your team has **3 agents**. Each agent runs as an independent process and communicates via the shared Context Bus and Mailbox system.

## 1. leo
- **Role**: The Brain & Orchestrator. Receives tasks first, decomposes into subtasks (max 3), delegates to Jerry, and synthesizes all results + Alic's evaluation into the final user-facing answer.
- **Model**: `minimax-m2.1` (flock)
- **Skills**: _base, planning
- **Fallback models**: deepseek-v3.2, qwen3-235b-thinking
- **Autonomy level**: 1

## 2. jerry
- **Role**: The Hands & Implementation. Carries out atomic subtasks assigned by Leo. Delivers raw, complete results (code, data, analysis) — never plans, never reviews.
- **Model**: `minimax-m2.1` (flock)
- **Skills**: _base, coding
- **Fallback models**: deepseek-v3.2, qwen3-235b-thinking
- **Autonomy level**: 1

## 3. alic
- **Role**: The Quality Advisor. Scores subtask outputs 1-10 using HLE-based dimensions (Accuracy, Calibration, Completeness, Technical Quality, Resource Usage). Writes memory entries for future task improvement.
- **Model**: `deepseek-v3.2` (flock)
- **Skills**: _base, review
- **Fallback models**: minimax-m2.1, qwen3-235b-thinking
- **Autonomy level**: 1

## Communication

- Agents coordinate via the **Context Bus** (shared key-value store) and **Mailbox** (P2P message passing).
- Address teammates by their **agent ID** when referencing their work.
- **Leo** decomposes tasks; **Jerry** implements them; **Alic** evaluates quality.
- Peer review scores feed into the reputation system, which influences task assignment priority.
