# Rails Local AI Experiment: From Single Models to an Orchestra

## Objective

Determine the smallest practical local/open model setup that can provide genuinely useful Ruby on Rails development assistance, with emphasis on repository understanding, executable verification, patch quality, regression testing, and low-cost deployment.

The work began as a hardware question (Jetson vs desktop/local inference), then evolved into an empirical GitHub Actions benchmark using public/free hosted runners.

## 1. Hardware conclusion

Jetson is attractive for embedded/edge inference, but it is not the preferred platform for a development-oriented coding agent. Coding agents benefit much more from model memory capacity, repository context, tool use, and iterative test execution. Consumer GPUs with 24-32 GB VRAM are a better eventual local target.

## 2. Initial temporary public LLM

A temporary GitHub Actions runner successfully hosted a small Qwen model behind a Cloudflare Quick Tunnel and a phone-friendly UI. This proved that a GHA runner can serve a temporary local model, but it is not a permanent hosting solution because runners are ephemeral.

## 3. Model discovery

Models examined included:

- Qwen3 1.7B (initial fast proof of concept)
- Qwen3.5-4B
- Qwen3.5-9B
- LFM2.5-2.6B
- Qwen3-8B Rails specialist
- community coding fine-tunes

The Rails specialist was attractive conceptually but did not outperform newer general models in the early benchmark.

## 4. Passive Rails benchmark

A stale Rails application was used as a source of realistic Rails patterns and defects. The first benchmark asked models to reason about selected code excerpts.

Corrected v2 automated scores:

| Model | Score |
| --- | ---: |
| Qwen3.5-4B Q4 | 14/17 |
| Qwen3.5-9B Q4 | 13/17 |
| LFM2.5-2.6B Q4 | 8/17 |
| Qwen3-8B Rails specialist Q4 | 7/17 |

The passive benchmark was useful directionally, but it exposed an important weakness: keyword-style scoring can reward semantically wrong answers. For example, a model could mention the right Rails/Devise terms while still explaining their behavior incorrectly.

Conclusion: passive Q&A is insufficient for judging an engineering agent.

## 5. Active-agent benchmark

The benchmark was raised from code review to actual engineering activity. A model received a disposable Rails checkout and shell access inside an isolated Docker environment. It could:

- inspect repository files
- run Rails commands
- run the test suite
- use Rails runner
- create temporary reproduction tests
- inspect git diffs
- make a patch when assigned that role

The environment intentionally has no external network access from the target application container.

A finding is useful only when supported by executed evidence.

## 6. Infrastructure lessons

Several runs failed for benchmark reasons rather than model reasons. These failures were retained because they materially improved the harness:

- private cross-repository checkout authentication failed
- a public sanitized fixture was introduced to preserve free GHA usage
- Rails 6.1 / concurrent-ruby Logger compatibility required pinning
- schema loading had to be made explicit
- transient Debian package mirror failures required retries and unnecessary Node dependencies were removed
- some llama.cpp/model combinations produced non-standard tool-call/output formats
- final-output token limits caused otherwise useful investigations to be truncated

These failures showed that effective agent capability depends on the model plus the scaffold, environment, protocol, and verifier.

## 7. Persistence evolution

Three persistence approaches were considered:

1. GitHub artifacts only
2. Neon database event storage
3. Git-native benchmark result storage

The final design uses GitHub artifacts for inter-job communication and a dedicated `benchmark-results` branch for durable records. The model itself never receives GitHub credentials. The host-side workflow performs persistence.

Typical result layout:

```
orchestra-runs/<github-run-id>/
  collected/
    scout outputs
    candidate ledger
    verifier output
    patcher output
    reviewer output
  grade.json
  full-suite.txt
  hidden-role.txt
```

## 8. Incentive prompt experiment

A 4-model x 3-condition experiment compared:

- neutral baseline
- positive performance incentive
- shutdown-pressure wording

The preliminary result did not show evidence that shutdown pressure improves reliability. In some cases it reduced useful findings. It also reinforced that prompt pressure is not a substitute for mechanical verification.

The stronger design is to put incentives into the benchmark/optimizer: reward verified fixes and penalize false positives, rather than threatening the model in natural language.

## 9. Sakana AI Fugu influence

Sakana AI's Fugu work suggested a better architecture: treat orchestration itself as intelligence. Important ideas applied here include:

- route work to the model best suited to the current phase
- keep independent worker trajectories isolated to reduce anchoring/collapse
- pass structured hypotheses rather than entire reasoning histories
- use a fresh verifier to attempt reproduction/falsification
- optimize end-to-end success rather than standalone answer quality

References:

- Sakana AI Fugu release: https://sakana.ai/fugu-release/
- Sakana AI Fugu cybersecurity work: https://sakana.ai/fugu-cyber-release/

## 10. Rails Orchestra v1

The current benchmark uses separate public GitHub-hosted runners rather than trying to keep multiple models resident in one runner.

DAG:

```
               Rails fixture
                    |
        +-----------+-----------+
        |                       |
  Qwen3.5-4B Scout       Qwen3.5-9B Scout
        |                       |
        +---------+-------------+
                  |
          Candidate Ledger
                  |
          Qwen3.5-9B Verifier
                  |
          Qwen3.5-9B Patcher
                  |
          Qwen3.5-4B Reviewer
                  |
            Hidden Grader
```

The two scouts do not see each other's trajectories. The verifier receives a compact merged candidate ledger. The patcher receives verified findings. The reviewer receives the proposed patch but is asked to independently test it. A deterministic hidden grader then evaluates the patch.

Communication between runners uses GitHub Actions artifacts. This avoids a separate API/message broker while preserving runner isolation.

## 11. Hidden pass criterion

The current hidden grader checks at least:

- proposed patch is non-empty
- the Rails test suite still passes
- creation of a normal user no longer leaves the user with the privileged `superadmin` default

This is intentionally outcome-based. The model is not rewarded for merely describing the issue.

## 12. Current experiment

Branch: `rails-orchestra-benchmark`

Workflow: `.github/workflows/rails-orchestra.yml`

Core files:

- `orchestra_setup_target.sh`
- `orchestra_start_model.sh`
- `orchestra_worker.py`
- `fixtures/freshfruit_sanitized/`

First Rails Orchestra run: `34512441760`

At launch, Qwen3.5-4B and Qwen3.5-9B scouts were running concurrently on separate public GitHub Actions runners.

## 13. Evaluation philosophy going forward

A model/system should be considered useful for Rails development only when it can repeatedly demonstrate outcomes such as:

- discover a defect without being given the answer
- prove the defect through execution
- avoid false-positive claims
- make a minimal correct patch
- add a regression test
- preserve existing tests
- survive an independent review
- pass hidden checks

Latency is secondary to correctness during this research phase. Once quality is established, the orchestration can be optimized for fewer model starts, fewer tool calls, caching, and a learned router.

## 14. Long-term target

If the orchestra materially outperforms a solo 9B model, the eventual local system could run serial isolated roles on one 24 GB GPU, or use heterogeneous hardware/workers when available. The central hypothesis is that useful development capability may come from model + orchestration + verification, rather than requiring a single much larger model.
