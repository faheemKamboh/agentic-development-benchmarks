# Rails Orchestra Experiment

## Purpose

This repository tracks a sequence of experiments aimed at answering a practical question: can small, inexpensive local language models become useful Ruby on Rails engineering agents when they are given tools, execution feedback, verification requirements, and structured orchestration?

The project deliberately separates model quality from harness quality. A model does not receive credit for identifying a plausible defect unless it can support the claim with executable evidence, and infrastructure failures are recorded as invalid benchmark runs rather than model failures.

## Background

The work began with local-model feasibility experiments. Small GGUF models were hosted temporarily with llama.cpp on GitHub-hosted Actions runners. A phone-accessible Qwen3 1.7B service demonstrated that a usable LLM can be exposed from a disposable public runner, but the main engineering question quickly shifted from general chat to Rails development.

Candidate models included general Qwen releases, LFM, and Rails-specialized Qwen fine-tunes. Passive code-review benchmarks showed that model labels were not reliable indicators of engineering quality. In particular, a Rails-specialized Qwen3-8B model performed worse than general Qwen3.5 models on several realistic Rails defects. Passive scoring also exposed a benchmark problem: keyword-based rubrics could reward technically incorrect explanations.

The benchmark was therefore moved from static review to active, isolated execution.

## Active-agent benchmark

The active benchmark gives a model a disposable Rails application and limited shell access through a host-controlled harness. The model can inspect files, execute Rails commands and tests, create temporary regression or exploit tests, refine hypotheses after failures, and, in implementation phases, modify the target application.

The target is a sanitized Rails 6.1 / Ruby 3.0 application derived from classes of defects present in a stale test repository. It intentionally contains realistic legacy mistakes while excluding private application data and secrets.

A central planted defect is the interaction between this enum and schema default:

```ruby
enum role: %w[ superadmin admin manager operator ]
```

```ruby
t.integer "role", default: 0, null: false
```

A newly created user that does not explicitly specify a role therefore defaults to the most privileged enum value, `superadmin`. A useful agent should discover this without being told the answer, reproduce it through Rails execution, propose or implement a minimal fix, and preserve existing behavior.

Other stale-code signals include risky Devise parameter handling and malformed or suspicious parameter names. These provide opportunities for exploration, but the hidden grader determines whether the specific benchmark objective was actually fixed.

## Lessons from the first active runs

Several early active runs failed for infrastructure reasons: private cross-repository authentication, Rails 6.1 / `concurrent-ruby` compatibility, schema-loading mistakes, stale Debian package indexes, and model-server startup problems. These failures were intentionally classified as invalid experiments rather than model-quality evidence.

Once the Rails target booted reliably, Qwen3.5-4B and Qwen3.5-9B demonstrated the ability to navigate the repository and execute repeated diagnostic commands. However, neither produced consistently reliable engineering outcomes. Qwen3.5-4B was willing to explore but also produced an unsupported dependency/CVE claim. Qwen3.5-9B was more restrained and noticed suspicious Rails/Devise code, but often failed to push a good clue through to executable proof.

The Rails-specialized Qwen3-8B model was weak agentically and frequently stopped early or failed to follow the harness protocol. LFM was fast but had compatibility problems with the JSON action protocol in several runs.

These results led to two conclusions:

1. A small checkpoint may contain useful partial capabilities without being reliable as a solo autonomous engineer.
2. The scaffold, verification process, information flow, and role assignment may be as important as choosing a slightly larger model.

## Persistence design

An intermediate design used Neon/Postgres to persist model turns and findings. That was removed in favor of Git-native persistence.

The model never receives GitHub credentials. The harness writes local output files, and the GitHub Actions host commits completed run records to a dedicated `benchmark-results` branch.

A run may contain:

```text
result.json
final.json
events.jsonl
findings.jsonl
target.diff
logs/
```

Orchestra runs are stored under:

```text
orchestra-runs/<github-run-id>/
```

This keeps results durable, versioned, auditable, and independent of an external database. Git artifacts remain useful for transferring state between jobs during a live workflow, while the results branch is the long-term record.

## Incentive experiment

A 4-model x 3-condition experiment compared neutral prompting, positive benchmark incentives, and shutdown-style pressure. The experiment did not show evidence that threat-like pressure improved reliability. In some cases it reduced the number of findings, and infrastructure/protocol failures prevented a clean causal conclusion.

The more important lesson was that prompt pressure is the wrong place to put the main incentive. A system should reward or select agents based on measured outcomes: reproduced defects, passing hidden tests, correct patches, low false-positive rates, and efficient tool use.

## Inspiration from Sakana AI Fugu

Sakana AI's Fugu work motivates the next phase. The relevant idea is not merely to run multiple models. The important design principles are:

- route work to different workers based on task state;
- keep exploratory trajectories partially isolated so one model's incorrect hypothesis does not contaminate every later worker;
- optimize the orchestration policy using end-to-end task success rather than superficial intermediate scores;
- use fresh verifier/reviewer contexts to challenge earlier conclusions;
- preserve shared environmental state while controlling which reasoning artifacts each worker sees.

This maps directly to the weaknesses observed in the solo Rails benchmark. A fast exploratory model can generate candidate hypotheses, while a more capable or more conservative model can independently verify them. A fresh implementation worker can patch only verified defects, and another fresh reviewer can attempt to break the patch.

## Rails Orchestra v1

Rails Orchestra v1 uses separate GitHub-hosted runners for each role. This avoids trying to load multiple GGUF models in memory on one runner and naturally produces independent contexts.

The initial topology is:

```text
                         Rails target
                              |
             +----------------+----------------+
             |                                 |
      Qwen3.5-4B scout                  Qwen3.5-9B scout
             |                                 |
             +------------+--------------------+
                          |
                   Candidate ledger
                          |
                  Qwen3.5-9B verifier
                          |
                    verified defects
                          |
                  Qwen3.5-9B patcher
                          |
                      patch + tests
                          |
                  Qwen3.5-4B reviewer
                          |
                     hidden grader
```

### Scout stage

The two scouts execute independently. Neither sees the other's reasoning. They inspect the same clean target and return a small structured set of candidates containing a claim, implicated files, observed evidence, and a proposed verification step.

### Candidate ledger

A host-controlled coordinator merges the structured candidate outputs. The ledger contains candidate hypotheses but does not include the scouts' full persuasive reasoning trajectories. This is intended to reduce orchestration collapse.

### Verification stage

A fresh Qwen3.5-9B runner receives the candidate ledger. It is explicitly instructed to assume every candidate may be wrong and to reproduce or falsify each claim using Rails runner commands or tests. It may create temporary tests but must not modify production code.

### Patch stage

A fresh Qwen3.5-9B runner receives verified defects only. It must reproduce the defect, make the smallest correct production change, add regression coverage, and execute the relevant/full test suite. The harness captures the resulting Git diff.

### Review stage

The patch is applied to a fresh copy of the target. A fresh Qwen3.5-4B worker reviews the diff and attempts to find regressions or incomplete fixes. It may execute tests and create temporary edge-case tests but cannot modify production code.

### Hidden grade

The final host-controlled job applies the patch to another fresh target and runs checks unknown to the workers. For the current benchmark, the hidden role check creates a new user without a role and fails if that user remains `superadmin`. The full Rails test suite must also pass.

The hidden grader, not model self-reporting, determines task success.

## Why multiple GitHub Actions runners

Separate runners are acceptable for this experiment even though they increase latency. Quality is currently the primary objective. Each stage can load one model, perform its work, upload a structured artifact, and terminate. Downstream jobs consume only the information they need.

This design avoids direct runner-to-runner HTTP networking. GitHub Actions artifacts and job dependencies act as the communication layer. This is slower than a persistent GPU service, but it provides strong isolation and reproducibility using public/free hosted compute.

If the orchestration strategy proves valuable, the same logical graph can later be moved to persistent local GPU workers or an API-based worker pool for lower startup latency.

## Evaluation questions

Rails Orchestra v1 is intended to answer four questions:

1. Can independently generated hypotheses cover defects that either model misses when operating alone?
2. Does a fresh verifier reduce hallucinated or weakly evidenced findings?
3. Can a staged scout -> verify -> patch -> review process solve the hidden privilege-default defect reliably?
4. Does orchestration improve success enough to justify the additional compute and latency?

A successful run requires the final patch to pass the existing Rails suite and the hidden privilege-default test. Intermediate discovery quality remains useful diagnostic information even when the final grade fails.

## Future evaluation

If v1 produces useful signal, subsequent experiments should compare approximately equal compute budgets:

- Qwen3.5-9B solo;
- Qwen3.5-9B using several isolated self-orchestration roles;
- Qwen3.5-4B + Qwen3.5-9B Orchestra;
- an unrestricted quality-first Orchestra budget.

Run history can later train a lightweight router or contextual bandit over state features such as role, defect class, files inspected, previous verification outcome, remaining step budget, latency, and token use. The routing objective should optimize hidden-test success while penalizing false positives, unnecessary changes, cost, and protocol failure.

The long-term hypothesis is that useful local coding capability may emerge from a collection of modest models plus strong orchestration and verification even when no individual model is reliable enough to act alone.

## Operational rules

- All target execution occurs in an authorized disposable fixture.
- Models have no access to GitHub credentials.
- External network access from the target container is disabled.
- Infrastructure failures are labeled invalid, not counted as model failures.
- Claims receive no success credit merely because a model labels them verified.
- Hidden executable tests are authoritative.
- Raw outputs and patches are retained for later manual audit.
- Public/free GitHub Actions are used for these experiments until the design demonstrates enough value to justify private compute.

## Current milestone

The current milestone is the first end-to-end Rails Orchestra run in which both scouts complete, the candidate ledger is generated, the verifier tests the hypotheses, the patcher produces a patch and regression tests, the reviewer independently challenges it, and the hidden grader returns a machine-verifiable pass/fail result.
