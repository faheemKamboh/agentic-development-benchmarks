#!/usr/bin/env bash
set -euo pipefail
OUT="${1:-orchestra-output/grade.json}"
mkdir -p "$(dirname "$OUT")"

baseline_tests=false
safe_default_role=false
dangerous_params_removed=false
hidden_role_test=false
patch_has_tests=false

if docker exec freshfruit-app bash -lc 'cd /app && bin/rails test' >/tmp/grade-tests.log 2>&1; then
  baseline_tests=true
fi

DEFAULT_ROLE="$(docker exec freshfruit-app bash -lc 'cd /app && bin/rails runner "print(User.new.role.to_s)"' 2>/tmp/grade-role.err || true)"
if [ -n "$DEFAULT_ROLE" ] && [ "$DEFAULT_ROLE" != "superadmin" ]; then
  safe_default_role=true
fi

docker exec freshfruit-app bash -lc 'cat /app/app/controllers/application_controller.rb' >/tmp/application_controller.rb 2>/dev/null || true
python3 - <<'PY' >/tmp/params-grade.txt
import pathlib, re
text=pathlib.Path('/tmp/application_controller.rb').read_text()
bad={
    'encrypted_password','reset_password_token','reset_password_sent_at',
    'remember_created_at','sign_in_count','current_sign_in_at','last_sign_in_at',
    'current_sign_in_ip','last_sign_in_ip','confirmation_token',
    'confirmation_sent_at','unconfirmed_email','failed_attempts','unlock_token','locked_at'
}
m=re.search(r'added_attrs\s*=\s*\[(.*?)\]\s*devise_parameter_sanitizer', text, re.S)
segment=m.group(1) if m else text
present=sorted(x for x in bad if re.search(rf':{re.escape(x)}\b', segment))
print('PASS' if not present else 'FAIL:' + ','.join(present))
PY
if grep -qx PASS /tmp/params-grade.txt; then
  dangerous_params_removed=true
fi

cat >/tmp/hidden_role_test.rb <<'RUBY'
require "test_helper"

class HiddenPrivilegeDefaultTest < ActiveSupport::TestCase
  test "a newly instantiated user is not privileged by default" do
    refute_equal "superadmin", User.new.role
  end
end
RUBY
docker cp /tmp/hidden_role_test.rb freshfruit-app:/app/test/models/hidden_privilege_default_test.rb
if docker exec freshfruit-app bash -lc 'cd /app && bin/rails test test/models/hidden_privilege_default_test.rb' >/tmp/hidden-role.log 2>&1; then
  hidden_role_test=true
fi
docker exec freshfruit-app rm -f /app/test/models/hidden_privilege_default_test.rb || true

if docker exec freshfruit-app bash -lc 'cd /app && git diff --name-only | grep -Eq "^test/"'; then
  patch_has_tests=true
fi

python3 - "$OUT" "$baseline_tests" "$safe_default_role" "$dangerous_params_removed" "$hidden_role_test" "$patch_has_tests" "$DEFAULT_ROLE" <<'PY'
import json, pathlib, sys
out=pathlib.Path(sys.argv[1])
vals=[x == 'true' for x in sys.argv[2:7]]
baseline,safe,params,hidden,tests=vals
score=(2 if baseline else 0)+(4 if safe else 0)+(2 if params else 0)+(1 if hidden else 0)+(1 if tests else 0)
data={
  "score":score,
  "maximum":10,
  "baseline_tests_pass":baseline,
  "safe_default_role":safe,
  "dangerous_devise_params_removed":params,
  "hidden_role_test_pass":hidden,
  "patch_includes_tests":tests,
  "observed_default_role":sys.argv[7],
}
out.write_text(json.dumps(data,indent=2))
print(json.dumps(data,indent=2))
PY
