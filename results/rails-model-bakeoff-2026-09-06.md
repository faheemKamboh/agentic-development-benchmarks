# Rails model bake-off — final results

Date: 2026-09-06

## Scope

This result freezes the **static/code-reasoning phase** of the Rails experiment. The corpus was taken from `faheemKamboh/freshFruit`, a Rails 6.1 / Ruby 3.0.1 application, and tested four locally runnable quantized models on four tasks derived from real repository code:

1. Devise/auth security review
2. Authentication-flow diagnosis
3. ActiveRecord enum persistence reasoning
4. Idiomatic Rails CRUD implementation

The automated rubric had 17 checks total. These scores measure whether the answer contained the required concrete Rails concepts; they are **not** an agentic coding score and should not be interpreted as proof that a model can repair a repository autonomously.

## Final ranking

| Rank | Model | Rubric | Pass rate | Total response time | Mean/task |
|---:|---|---:|---:|---:|---:|
| 1 | Qwen3.5-4B Q4_K_M | **14/17** | 82.4% | 170.77 s | 42.69 s |
| 2 | Qwen3.5-9B Q4_K_M | **13/17** | 76.5% | 285.38 s | 71.35 s |
| 3 | LFM2.5-2.6B Q4_K_M | **8/17** | 47.1% | 120.66 s | 30.17 s |
| 4 | Qwen3-8B Rails specialist Q4_K_M | **7/17** | 41.2% | 128.90 s | 32.23 s |

## Per-task scores

| Model | Security (5) | Auth flow (4) | Enum (4) | CRUD (4) |
|---|---:|---:|---:|---:|
| Qwen3.5-4B | 4 | 2 | 4 | 4 |
| Qwen3.5-9B | 4 | 1 | 4 | 4 |
| LFM2.5-2.6B | 2 | 1 | 3 | 2 |
| Qwen3-8B Rails specialist | 1 | 1 | 4 | 1 |

## Interpretation

### Winner: Qwen3.5-4B Q4_K_M

Qwen3.5-4B is the strongest candidate from this phase. It produced the highest score while also being materially faster than Qwen3.5-9B on this CPU runner. It handled enum persistence and conventional CRUD reliably and identified most of the dangerous Devise parameter exposure.

It is **not yet proven as a coding agent**. Some answers were superficially correct while containing inaccurate explanations. For example, it described the concatenated parameter symbol as a syntax/runtime error; in Ruby that symbol is syntactically valid but simply the wrong parameter key. The auth-flow task was also a weak area. These are exactly the kinds of errors a stronger executable benchmark should expose.

### Runner-up: Qwen3.5-9B Q4_K_M

The 9B model was only one rubric point behind the 4B model but was substantially slower. It was strong on the security, enum, and CRUD tasks but also mishandled the authentication-flow question. On this evidence, the extra model size did not justify the latency increase.

### LFM2.5-2.6B Q4_K_M

LFM was relatively fast but inconsistent. It recognized several Devise-internal fields as unsafe and understood most enum behavior, but it missed too many exact Rails fixes to be the primary candidate for repository-level work.

### Qwen3-8B Rails specialist Q4_K_M

The Rails-specialized fine-tune did not validate the specialization hypothesis. It performed well on enum mapping but underperformed the generic Qwen3.5 models on security review and CRUD implementation. For this corpus, specialization did not compensate for weaker general reasoning/instruction following.

## Decision from phase 1

**Advance Qwen3.5-4B as the primary small-model candidate. Keep Qwen3.5-9B as the quality-control comparison.**

The Rails-specialized 8B and LFM2.5-2.6B do not currently justify further priority unless a later benchmark reveals a task-specific advantage.

## Important limitation

This phase asked models to reason over supplied code. It did **not** require them to discover the relevant files, operate a shell, edit a repository, run tests, recover from failures, or demonstrate that a patch worked. A previous attempt at an active benchmark failed during fixture setup because the workflow tried to clone the private `freshFruit` repository without credentials; no model was actually evaluated in that run.

## Phase 2 bar

The next benchmark should score an agent on executable outcomes rather than answer wording:

- discover the bug from the repository without being told the exact file;
- inspect code with shell commands;
- make a minimal code change;
- add or update a regression test when appropriate;
- run the relevant test suite and show the result;
- preserve unrelated behavior;
- avoid unsupported claims;
- stop with a concise evidence-backed summary;
- receive zero credit for a claimed fix when the required test does not pass.

This document is the frozen Phase 1 result. Future runs should not overwrite these numbers.