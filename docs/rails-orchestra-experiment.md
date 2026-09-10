# Rails Local-Agent Experiment: From Single Models to a Multi-Runner Orchestra

## Objective

This repository is being used to answer a practical question: **what is the smallest, inexpensive local/open model setup that can behave like a useful Ruby on Rails development agent rather than merely answer coding questions?**

The experiment deliberately prioritizes real engineering outcomes over benchmark-style prose. A useful agent should be able to inspect a repository, form hypotheses, execute Rails commands and tests, reproduce defects, make minimal fixes, add regression coverage, and survive deterministic hidden checks.

The target is a sanitized Rails 6.1 / Ruby 3.0 legacy application fixture modeled on defects observed in an old application. The public fixture contains no private repository content, credentials, or production data.

## Why GitHub Actions

GitHub Actions provides disposable Linux machines that are adequate for CPU inference with quantized 2B-9B models. Public-repository hosted runners let us explore the design before spending private-runner quota or purchasing hardware.

The limitations are intentional parts of the study:

- CPU inference is slow compared with a GPU workstation.
- Each runner is ephemeral.
- Model startup/download time is material.
- Separate jobs do not share RAM or local state.
- Cross-runner collaboration therefore needs explicit artifacts/state transfer.

These constraints make the system a useful prototype for a future distributed or local multi-agent deployment.

## Phase 1: Temporary phone-access LLM

The first experiment ran a small Qwen model under `llama.cpp` on a GitHub-hosted runner and exposed a temporary web UI through an outbound Cloudflare tunnel. The experiment proved that a model could be hosted temporarily and accessed from a phone, but GitHub Actions is not suitable as permanent hosting because the runner and tunnel disappear when the job terminates.

The main conclusion was that Jetson-class 8 GB hardware and very small 1B-2B models were not the right target for a serious coding assistant. Attention shifted to 4B-9B current-generation models and to measuring development capability rather than chat quality.

## Phase 2: Static Rails model bake-off

Several models were tested on source-grounded Rails questions derived from the sanitized legacy application.

Corrected static benchmark results:

| Model | Score |
| --- | ---: |
| Qwen3.5-4B Q4_K_M | 14 / 17 |
| Qwen3.5-9B Q4_K_M | 13 / 17 |
| LFM2.5-2.6B Q4_K_M | 8 / 17 |
| Qwen3-8B Rails specialist Q4_K_M | 7 / 17 |

The static benchmark exposed a major flaw in question/keyword scoring: a model can mention the right concepts while making an incorrect claim. Therefore the experiment moved from passive review to executable proof.

## Phase 3: Active single-agent benchmark

The next harness gave a model shell access to a disposable Rails app. Models were instructed to inspect the application, execute Rails/tests/scanners, and persist findings as they were discovered.

The active harness introduced the protocol:

```json
{"action":"shell","command":"...","purpose":"..."}
```

```json
{"action":"finding","title":"...","severity":"high","claim":"...","evidence_steps":[1,2],"evidence_summary":"...","fix":"..."}
```

```json
{"action":"final","summary":"..."}
```

This was designed to solve final-response truncation by persisting findings incrementally rather than waiting for one long final answer.

### Findings from the active runs

- Qwen3.5-4B was willing to explore extensively, but could make confident unsupported security/CVE claims.
- Qwen3.5-9B appeared more restrained, but sometimes failed to pursue a suspicious clue far enough to prove the underlying issue.
- The Rails-specific Qwen3-8B fine-tune did not outperform the newer general Qwen models and had weaker agent behavior in several runs.
- LFM2.5 was fast, but its native tool-call/output behavior did not always match the harness protocol.

This led to a stronger principle: **evidence must be semantically validated, not merely associated with a shell-step number.**

## Incentive-prompt experiment

A later matrix compared three prompting conditions:

1. neutral baseline;
2. positive incentive language emphasizing future task selection for verified work;
3. shutdown-pressure language stating that unsupported claims would cause the run to fail and the model instance to be terminated after evaluation.

The result did not show evidence that pressure improves engineering quality. In at least one case the pressure condition produced fewer useful findings. More importantly, prompt incentives cannot substitute for deterministic evaluation.

The experiment therefore moved the incentive mechanism out of the prompt and into the system design: workers that produce verified outcomes should receive more routing probability or future compute; workers that hallucinate should receive less.

## Persistence evolution

The benchmark briefly explored using Neon/PostgreSQL as an event store for turns, findings, and shell results. This would provide durable structured state, but it introduced an unnecessary external dependency and did not solve generation truncation itself.

The design was simplified to Git-native persistence:

- model output is written locally as `events.jsonl`, `findings.jsonl`, `result.json`, and `report.md`;
- target modifications are captured as `target-diff.txt`;
- the workflow host, not the model, checkpoints results to a dedicated `benchmark-results` branch;
- models never receive GitHub credentials.

This preserves every run in a versioned, auditable form with no database secret.

## Why a Fugu-style orchestra

Sakana AI's Fugu work suggests that effective agent capability depends not only on the base model but also on routing, context isolation, worker specialization, and verification.

The most relevant ideas for this project are:

- use different workers for different stages;
- isolate independent exploratory trajectories so one worker's bad hypothesis does not contaminate another;
- transfer concise structured state rather than full hidden reasoning;
- use fresh verifier/reviewer contexts;
- optimize routing based on end-to-end task success rather than persuasive intermediate text.

This maps directly to the failure modes seen in the single-agent tests.

## Rails Orchestra Pilot v1

The first multi-runner pilot uses separate GitHub-hosted runners and GitHub artifacts as the inter-worker message bus.

```text
                         Rails target
                              |
              +---------------+---------------+
              |                               |
        Scout A: Qwen 4B                Scout B: Qwen 9B
        independent search              independent search
              |                               |
              +---------- findings -----------+
                              |
                       Candidate ledger
                              |
                    Verifier: fresh Qwen 9B
                    reproduce or falsify
                              |
                        verified findings
                              |
                     Patcher: fresh Qwen 9B
                    minimal fix + tests
                              |
                     Reviewer: fresh Qwen 4B
                       try to break patch
                              |
                    deterministic hidden grader
```

A **Qwen3.5-9B solo baseline** runs in parallel against the same target and deterministic grader.

### Context isolation

Scouts do not see each other's reasoning. The coordinator transfers only structured candidates such as claim, severity, evidence summary, and proposed fix. The verifier receives those candidates as untrusted hypotheses and must reproduce or falsify them independently.

The patcher receives only findings that the verifier chose to persist. The reviewer receives the resulting patch and compact patcher claims, not the patcher's full reasoning trajectory.

### Read/write policy

- scouts: read-only target;
- verifier: read-only target;
- patcher: may modify target and add tests;
- reviewer: read-only patched target;
- solo baseline: may modify target and add tests.

The shell harness rejects common write operations in read-only stages.

## Hidden deterministic grading

The model cannot see the grader script from inside the Rails target container. The first hidden grader checks:

- the normal Rails test suite still passes;
- a newly instantiated user no longer defaults to the privileged `superadmin` role;
- dangerous internal Devise fields are removed from the permitted-parameter list;
- an independent hidden privilege-default test passes;
- the proposed patch adds regression-test coverage.

Current pilot scoring is 10 points:

| Check | Points |
| --- | ---: |
| Existing Rails tests pass | 2 |
| Safe default role | 4 |
| Dangerous Devise params removed | 2 |
| Hidden privilege-default test passes | 1 |
| Patch adds tests | 1 |

The score is intentionally outcome-based. A persuasive report receives no credit if the patched application fails the hidden checks.

## Pilot comparison

The initial orchestra run is **quality-first**, not compute-matched.

It compares:

- `solo-9b`: one Qwen3.5-9B agent with a larger turn budget;
- `orchestra`: two parallel scouts, a 9B verifier, a 9B patcher, and a 4B adversarial reviewer.

If the orchestra shows a quality advantage, the next experiment should normalize total token/tool budgets to answer whether orchestration is also compute-efficient.

## Success criteria

A strong result is not merely finding a suspicious line. The system should:

1. independently discover a real defect;
2. demonstrate the behavior with executable evidence;
3. avoid unsupported vulnerability claims;
4. produce a minimal fix;
5. add a regression test;
6. keep the existing suite green;
7. pass hidden checks unavailable to the worker;
8. leave an auditable transcript and patch.

The most important known hidden issue in the fixture is intentionally not included in worker prompts. The system must discover it from application behavior/source and prove it.

## Result storage

Each workflow stage publishes an artifact. At workflow completion the host collects all available artifacts and commits them to:

```text
benchmark-results
  runs/
    <github-run-id>/
      rails-orchestra-pilot-v1/
        experiment-summary.json
        all-orchestra-artifacts/
          ...
```

Each worker directory can contain:

```text
events.jsonl
findings.jsonl
prompt.json
result.json
report.md
target-diff.txt
target-status.txt
model.log
postgres.log
run-metadata.json
```

No model is given repository credentials.

## Next steps after Pilot v1

If the orchestra beats the solo baseline, subsequent work should test:

1. equal-compute solo vs orchestra runs;
2. repeated seeds to estimate variance;
3. additional Rails fixtures and failure classes;
4. candidate-ranking and deduplication policies;
5. a learned or bandit router that chooses 4B vs 9B based on state;
6. stop conditions that avoid unnecessary verifier/patcher calls;
7. GPU/local workstation deployment once the architecture proves useful.

If the orchestra does not improve hidden-test performance, the result is also useful: it would suggest that models in this size range lack enough underlying capability for orchestration alone to recover reliable Rails engineering.

## Guiding principle

The project is no longer searching only for a single "Rails-specialist model." It is testing whether **small, inexpensive models can become a useful software-engineering system when their exploration, verification, implementation, and review responsibilities are separated and evaluated against real executable outcomes.**
