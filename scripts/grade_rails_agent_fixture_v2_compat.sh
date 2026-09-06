#!/usr/bin/env bash
set -euo pipefail
sed 's/bash -lc/bash -c/g' scripts/grade_rails_agent_fixture_v2.sh > /tmp/grade_rails_agent_fixture_v2.sh
bash /tmp/grade_rails_agent_fixture_v2.sh
