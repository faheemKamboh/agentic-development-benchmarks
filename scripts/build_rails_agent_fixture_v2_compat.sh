#!/usr/bin/env bash
set -euo pipefail
sed \
  -e 's/ruby:3\.0\.1-bullseye/ruby:3.0.6-bullseye/' \
  -e 's/bash -lc/bash -c/g' \
  scripts/build_rails_agent_fixture_v2.sh > /tmp/build_rails_agent_fixture_v2.sh
bash /tmp/build_rails_agent_fixture_v2.sh
