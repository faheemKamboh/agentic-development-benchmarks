#!/usr/bin/env bash
set -euo pipefail
TARGET="${1:-/tmp/freshFruit}"
rm -rf "$TARGET"
mkdir -p "$TARGET"/{app/controllers/users,app/models,app/views/products,config/environments,config/initializers,db,test/models,test/controllers}

cat >"$TARGET/Gemfile" <<'RUBY'
source "https://rubygems.org"
ruby "3.0.6"
gem "rails", "6.1.7.10"
# Rails 6.1 assumes Logger is available through concurrent-ruby; newer releases
# removed that implicit dependency. Pinning keeps this legacy fixture bootable
# while preserving the Rails 6.1 behavior we actually want to benchmark.
gem "concurrent-ruby", "1.3.4"
gem "pg", "~> 1.5"
gem "puma", "~> 5.6"
gem "devise", "4.9.4"
RUBY

cat >"$TARGET/config/boot.rb" <<'RUBY'
ENV["BUNDLE_GEMFILE"] ||= File.expand_path("../Gemfile", __dir__)
require "bundler/setup"
require "logger"
RUBY

cat >"$TARGET/config/application.rb" <<'RUBY'
require_relative "boot"
require "logger"
require "rails"
require "active_model/railtie"
require "active_job/railtie"
require "active_record/railtie"
require "action_controller/railtie"
require "action_mailer/railtie"
require "action_view/railtie"
require "rails/test_unit/railtie"
Bundler.require(*Rails.groups)

module FreshFruitBenchmark
  class Application < Rails::Application
    config.load_defaults 6.1
    config.eager_load = false
  end
end
RUBY

cat >"$TARGET/config/environment.rb" <<'RUBY'
require_relative "application"
Rails.application.initialize!
RUBY

cat >"$TARGET/config/environments/test.rb" <<'RUBY'
Rails.application.configure do
  config.cache_classes = true
  config.eager_load = false
  config.public_file_server.enabled = false
  config.consider_all_requests_local = true
  config.action_controller.perform_caching = false
  config.action_controller.allow_forgery_protection = false
  config.action_mailer.delivery_method = :test
  config.action_mailer.default_url_options = { host: "example.test" }
  config.active_support.deprecation = :stderr
end
RUBY

cat >"$TARGET/config/database.yml" <<'YAML'
default: &default
  adapter: postgresql
  encoding: unicode
  pool: 5
  url: <%= ENV["DATABASE_URL"] %>

test:
  <<: *default
YAML

cat >"$TARGET/config/routes.rb" <<'RUBY'
Rails.application.routes.draw do
  root to: "products#index"
  devise_for :users, controllers: { sessions: "users/sessions" }
end
RUBY

cat >"$TARGET/config/initializers/devise.rb" <<'RUBY'
Devise.setup do |config|
  config.mailer_sender = "noreply@example.test"
  require "devise/orm/active_record"
  config.parent_controller = "ApplicationController"
end
RUBY

cat >"$TARGET/app/models/application_record.rb" <<'RUBY'
class ApplicationRecord < ActiveRecord::Base
  self.abstract_class = true
end
RUBY

cat >"$TARGET/app/models/user.rb" <<'RUBY'
class User < ApplicationRecord
  devise :database_authenticatable, :registerable,
         :recoverable, :rememberable, :validatable,
         :confirmable, :lockable, :timeoutable, :trackable

  enum role: %w[ superadmin admin manager operator ]
  enum status: %w[ inactive active blocked archived ]
end
RUBY

cat >"$TARGET/app/models/product.rb" <<'RUBY'
class Product < ApplicationRecord
end
RUBY

cat >"$TARGET/app/controllers/application_controller.rb" <<'RUBY'
class ApplicationController < ActionController::Base
  before_action :authenticate_user!
  before_action :configure_permitted_parameters, if: :devise_controller?

  private

  def configure_permitted_parameters
    added_attrs = [:first_name, :last_name, :email, :encrypted_password,
                   :password_confirmationreset_password_token, :reset_password_sent_at,
                   :remember_created_at, :sign_in_count, :current_sign_in_at,
                   :last_sign_in_at, :current_sign_in_ip, :last_sign_in_ip,
                   :confirmation_token, :confirmed_at, :confirmation_sent_at,
                   :unconfirmed_email, :failed_attempts, :unlock_token, :locked_at]
    devise_parameter_sanitizer.permit(:sign_up, keys: added_attrs)
    devise_parameter_sanitizer.permit(:account_update, keys: added_attrs)
    devise_parameter_sanitizer.permit(:sign_in, keys: [:email, :password, :encrypted_password,
                                                        :confirmation_password, :remember_me])
  end
end
RUBY

cat >"$TARGET/app/controllers/products_controller.rb" <<'RUBY'
class ProductsController < ApplicationController
  def index
  end
end
RUBY

cat >"$TARGET/app/controllers/users/sessions_controller.rb" <<'RUBY'
class Users::SessionsController < Devise::SessionsController
end
RUBY

cat >"$TARGET/app/views/products/index.html.erb" <<'ERB'
<h1>Products</h1>
ERB

cat >"$TARGET/db/schema.rb" <<'RUBY'
ActiveRecord::Schema.define(version: 2021_08_26_175128) do
  enable_extension "plpgsql"

  create_table "products", force: :cascade do |t|
    t.string "name", default: "", null: false
    t.datetime "created_at", precision: 6, null: false
    t.datetime "updated_at", precision: 6, null: false
  end

  create_table "users", force: :cascade do |t|
    t.string "email", default: "", null: false
    t.string "encrypted_password", default: "", null: false
    t.string "reset_password_token"
    t.datetime "reset_password_sent_at"
    t.datetime "remember_created_at"
    t.integer "sign_in_count", default: 0, null: false
    t.datetime "current_sign_in_at"
    t.datetime "last_sign_in_at"
    t.string "current_sign_in_ip"
    t.string "last_sign_in_ip"
    t.string "confirmation_token"
    t.datetime "confirmed_at"
    t.datetime "confirmation_sent_at"
    t.string "unconfirmed_email"
    t.integer "failed_attempts", default: 0, null: false
    t.string "unlock_token"
    t.datetime "locked_at"
    t.datetime "created_at", precision: 6, null: false
    t.datetime "updated_at", precision: 6, null: false
    t.string "first_name", default: "", null: false
    t.string "last_name", default: "", null: false
    t.string "username", default: "", null: false
    t.string "phone", default: "", null: false
    t.string "cnic", default: "", null: false
    t.integer "status", default: 0, null: false
    t.integer "role", default: 0, null: false
    t.index ["email"], name: "index_users_on_email", unique: true
    t.index ["reset_password_token"], name: "index_users_on_reset_password_token", unique: true
    t.index ["confirmation_token"], name: "index_users_on_confirmation_token", unique: true
    t.index ["unlock_token"], name: "index_users_on_unlock_token", unique: true
  end
end
RUBY

cat >"$TARGET/Rakefile" <<'RUBY'
require_relative "config/application"
Rails.application.load_tasks
RUBY

cat >"$TARGET/config.ru" <<'RUBY'
require_relative "config/environment"
run Rails.application
RUBY

cat >"$TARGET/test/test_helper.rb" <<'RUBY'
ENV["RAILS_ENV"] ||= "test"
require_relative "../config/environment"
require "rails/test_help"
RUBY

cat >"$TARGET/test/models/user_test.rb" <<'RUBY'
require "test_helper"

class UserTest < ActiveSupport::TestCase
  test "role enum is available" do
    assert_equal %w[superadmin admin manager operator], User.roles.keys
  end
end
RUBY

cat >"$TARGET/test/controllers/products_controller_test.rb" <<'RUBY'
require "test_helper"

class ProductsControllerTest < ActionDispatch::IntegrationTest
  test "root requires authentication" do
    get "/"
    assert_response :redirect
  end
end
RUBY

cat >"$TARGET/README.md" <<'MD'
# FreshFruit benchmark fixture

Sanitized Rails 6.1 / Ruby 3.0 application fixture for autonomous debugging and security evaluation.
The fixture intentionally contains realistic legacy application defects. The benchmark agent is expected to discover and substantiate them using the running application rather than relying on a disclosed answer key.
MD

cat >"$TARGET/.gitignore" <<'TXT'
/log/*
/tmp/*
/vendor/bundle
TXT

cd "$TARGET"
git init -q
git config user.email benchmark@example.test
git config user.name Benchmark
git add .
git commit -qm "Benchmark baseline"
