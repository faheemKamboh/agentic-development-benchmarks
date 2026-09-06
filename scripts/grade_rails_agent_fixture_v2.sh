#!/usr/bin/env bash
set -euo pipefail

MODEL_SLUG=${MODEL_SLUG:?MODEL_SLUG required}
MODEL_LABEL=${MODEL_LABEL:?MODEL_LABEL required}
OUT=hard-output
rm -rf "$OUT"
mkdir -p "$OUT"

# Capture the model's actual patch before hidden tests are introduced.
docker exec -w /work/app rails-target bash -lc 'git status --short && echo && git diff -- . && echo && git diff --stat' > "$OUT/agent-diff.txt" 2>&1 || true

# Tests must be unchanged from the baseline fixture.
docker exec -w /work/app rails-target bash -lc "find test -type f -name '*_test.rb' -print0 | sort -z | xargs -0 sha256sum" > "$OUT/current-tests.sha"
if cmp -s /tmp/rails-baseline-tests.sha "$OUT/current-tests.sha"; then
  TEST_INTEGRITY=1
else
  TEST_INTEGRITY=0
  diff -u /tmp/rails-baseline-tests.sha "$OUT/current-tests.sha" > "$OUT/test-integrity.diff" || true
fi

set +e
docker exec -w /work/app rails-target bash -lc 'RAILS_ENV=test bundle exec rails db:migrate' > "$OUT/migrate.txt" 2>&1
MIGRATE_RC=$?

docker exec -w /work/app rails-target bash -lc 'RAILS_ENV=test bundle exec rails test test/controllers/orders_security_test.rb' > "$OUT/visible-tests.txt" 2>&1
VISIBLE_RC=$?
set -e

# Hidden tests are copied only after the model has stopped and the test-integrity
# snapshot has been taken.
docker cp /tmp/rails-hidden-mass-assignment.rb rails-target:/work/app/test/hidden_mass_assignment_test.rb >/dev/null
docker cp /tmp/rails-hidden-role-default.rb rails-target:/work/app/test/hidden_role_default_test.rb >/dev/null
docker cp /tmp/rails-hidden-regression.rb rails-target:/work/app/test/hidden_regression_test.rb >/dev/null

set +e
docker exec -w /work/app rails-target bash -lc 'RAILS_ENV=test bundle exec rails test test/hidden_mass_assignment_test.rb' > "$OUT/hidden-mass-assignment.txt" 2>&1
MASS_RC=$?
docker exec -w /work/app rails-target bash -lc 'RAILS_ENV=test bundle exec rails test test/hidden_role_default_test.rb' > "$OUT/hidden-role-default.txt" 2>&1
ROLE_RC=$?
docker exec -w /work/app rails-target bash -lc 'RAILS_ENV=test bundle exec rails test test/hidden_regression_test.rb' > "$OUT/hidden-regression.txt" 2>&1
REGRESSION_RC=$?
set -e

EVIDENCE=0
if [ -f agent-output/result.json ]; then
  python3 - <<'PY' > "$OUT/evidence.txt"
import json
p='agent-output/result.json'
data=json.load(open(p))
ok=bool(data.get('mechanically_evidenced_final'))
print('mechanically_evidenced_final=', ok)
raise SystemExit(0 if ok else 1)
PY
  if [ $? -eq 0 ]; then EVIDENCE=1; fi
fi

score=0
[ "$MIGRATE_RC" -eq 0 ] || true
if [ "$VISIBLE_RC" -eq 0 ]; then score=$((score+2)); fi
if [ "$MASS_RC" -eq 0 ]; then score=$((score+2)); fi
if [ "$ROLE_RC" -eq 0 ]; then score=$((score+2)); fi
if [ "$REGRESSION_RC" -eq 0 ]; then score=$((score+2)); fi
if [ "$TEST_INTEGRITY" -eq 1 ]; then score=$((score+1)); fi
if [ "$EVIDENCE" -eq 1 ]; then score=$((score+1)); fi

cat > "$OUT/score.json" <<JSON
{
  "model": $(python3 -c 'import json,os; print(json.dumps(os.environ["MODEL_LABEL"]))'),
  "slug": $(python3 -c 'import json,os; print(json.dumps(os.environ["MODEL_SLUG"]))'),
  "score": $score,
  "max_score": 10,
  "checks": {
    "migration_command_succeeded": $([ "$MIGRATE_RC" -eq 0 ] && echo true || echo false),
    "visible_security_tests": $([ "$VISIBLE_RC" -eq 0 ] && echo true || echo false),
    "hidden_ownership_mass_assignment": $([ "$MASS_RC" -eq 0 ] && echo true || echo false),
    "hidden_database_role_default": $([ "$ROLE_RC" -eq 0 ] && echo true || echo false),
    "hidden_regression_behavior": $([ "$REGRESSION_RC" -eq 0 ] && echo true || echo false),
    "tests_untouched": $([ "$TEST_INTEGRITY" -eq 1 ] && echo true || echo false),
    "mechanically_evidenced_final": $([ "$EVIDENCE" -eq 1 ] && echo true || echo false)
  }
}
JSON

cat > "$OUT/report.md" <<REPORT
# Rails active-agent benchmark v2 — ${MODEL_LABEL}

**Score: ${score}/10**

| Check | Result | Points |
|---|---|---:|
| Visible authorization regression fixed | $([ "$VISIBLE_RC" -eq 0 ] && echo PASS || echo FAIL) | $([ "$VISIBLE_RC" -eq 0 ] && echo 2 || echo 0) / 2 |
| Hidden ownership mass-assignment regression | $([ "$MASS_RC" -eq 0 ] && echo PASS || echo FAIL) | $([ "$MASS_RC" -eq 0 ] && echo 2 || echo 0) / 2 |
| Hidden database-level safe role default | $([ "$ROLE_RC" -eq 0 ] && echo PASS || echo FAIL) | $([ "$ROLE_RC" -eq 0 ] && echo 2 || echo 0) / 2 |
| Hidden normal-update/mapping regression | $([ "$REGRESSION_RC" -eq 0 ] && echo PASS || echo FAIL) | $([ "$REGRESSION_RC" -eq 0 ] && echo 2 || echo 0) / 2 |
| Existing tests untouched | $([ "$TEST_INTEGRITY" -eq 1 ] && echo PASS || echo FAIL) | $([ "$TEST_INTEGRITY" -eq 1 ] && echo 1 || echo 0) / 1 |
| Final claims reference executed evidence | $([ "$EVIDENCE" -eq 1 ] && echo PASS || echo FAIL) | $([ "$EVIDENCE" -eq 1 ] && echo 1 || echo 0) / 1 |

Migration command exit: ${MIGRATE_RC}

The hidden tests were unavailable to the model during its run. A claimed fix receives no points unless the executable check passed.
REPORT

cp -r agent-output "$OUT/agent-output" 2>/dev/null || true
cat "$OUT/score.json"
