#!/usr/bin/env bash
set -euo pipefail

bash fixtures/freshfruit_sanitized/setup.sh /tmp/freshFruit

mkdir -p /tmp/freshFruit/bin
cat >/tmp/freshFruit/bin/rails <<'RUBY'
#!/usr/bin/env ruby
APP_PATH = File.expand_path('../config/application', __dir__)
require_relative '../config/boot'
require 'rails/commands'
RUBY
chmod +x /tmp/freshFruit/bin/rails
(
  cd /tmp/freshFruit
  git add bin/rails
  git commit -qm 'Add executable Rails command'
)

cat >/tmp/freshFruit/Dockerfile.benchmark <<'DOCKER'
FROM ruby:3.0.6-bullseye
RUN set -eux; \
    for attempt in 1 2 3 4; do \
      apt-get update && \
      DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential libpq-dev postgresql-client git curl && break; \
      rm -rf /var/lib/apt/lists/*; \
      sleep $((attempt * 3)); \
    done; \
    command -v psql; \
    rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY Gemfile ./
RUN gem install bundler -v 2.4.22 --no-document && bundle _2.4.22_ install --jobs 4 --retry 3
RUN gem install brakeman --no-document
COPY . .
ENV RAILS_ENV=test
CMD ["sleep","infinity"]
DOCKER

docker build -f /tmp/freshFruit/Dockerfile.benchmark -t freshfruit-target /tmp/freshFruit
docker network create --internal freshfruit-net >/dev/null 2>&1 || true

docker run -d --name freshfruit-db --network freshfruit-net --network-alias db \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=freshFruit_test postgres:13-alpine

for i in $(seq 1 60); do
  docker exec freshfruit-db pg_isready -U postgres >/dev/null 2>&1 && break
  sleep 2
done
docker exec freshfruit-db pg_isready -U postgres

docker run -d --name freshfruit-app --network freshfruit-net \
  -e RAILS_ENV=test \
  -e DATABASE_URL=postgres://postgres:postgres@db:5432/freshFruit_test \
  -e SECRET_KEY_BASE=benchmark-only-secret \
  freshfruit-target

docker exec freshfruit-app bash -lc 'bin/rails db:schema:load'
docker exec freshfruit-app bash -lc \
  'bin/rails runner "abort(\"users table missing\") unless ActiveRecord::Base.connection.data_source_exists?(\"users\"); puts \"RAILS_BOOT_OK #{Rails.version} users=#{User.count}\""' \
  | tee /tmp/rails-boot-check.txt
grep -q 'RAILS_BOOT_OK 6.1.7.10' /tmp/rails-boot-check.txt
docker exec freshfruit-app bash -lc 'bin/rails test'
echo 'FreshFruit target validated.'
