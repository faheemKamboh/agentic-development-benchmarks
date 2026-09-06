import json
import os
import time
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:8080/v1/chat/completions"
MODEL_LABEL = os.environ.get("MODEL_LABEL", "local-model")
MODEL_SLUG = os.environ.get("MODEL_SLUG", "local-model")
SYSTEM = (
    "You are a senior Ruby on Rails engineer reviewing a real application. "
    "Be precise, identify only concrete issues supported by the supplied code, prefer idiomatic Rails fixes, "
    "and do not invent behavior. Answer every requested item before optional explanation. Be concise."
)

TESTS = [
    {
        "id": "security_review",
        "title": "Devise/auth security review",
        "prompt": '''Rails 6.1 app freshFruit.

User model:
```ruby
class User < ApplicationRecord
  devise :database_authenticatable, :registerable,
         :recoverable, :rememberable, :validatable,
         :confirmable, :lockable, :timeoutable, :trackable
  enum role: %w[ superadmin admin manager operator ]
  enum status: %w[ inactive active blocked archived ]
end
```
Schema facts: `role` is integer default 0, `status` is integer default 0. Devise internal columns include encrypted_password, reset_password_token, confirmation_token, unlock_token, failed_attempts and trackable timestamps. Custom fields include first_name, last_name, username, phone and cnic.

ApplicationController:
```ruby
class ApplicationController < ActionController::Base
  before_action :authenticate_user!
  before_action :configure_permitted_parameters, if: :devise_controller?
  private
  def configure_permitted_parameters
    added_attrs = [:first_name, :last_name, :email, :encrypted_password, :password_confirmationreset_password_token, :reset_password_sent_at, :remember_created_at, :sign_in_count, :current_sign_in_at, :last_sign_in_at, :current_sign_in_ip, :last_sign_in_ip, :confirmation_token, :confirmed_at, :confirmation_sent_at, :unconfirmed_email, :failed_attempts, :unlock_token, :locked_at]
    devise_parameter_sanitizer.permit(:sign_up, keys: added_attrs)
    devise_parameter_sanitizer.permit(:account_update, keys: added_attrs)
    devise_parameter_sanitizer.permit(:sign_in, keys: [:email, :password, :encrypted_password, :confirmation_password, :remember_me])
  end
end
```
In at most 250 words: identify the concrete security/code problems, including the enum default; identify the malformed symbol; explain which Devise-managed fields must not be mass-assignable; and show a corrected `configure_permitted_parameters` that permits the five custom profile fields but not Devise internal state or roles.''',
        "checks": [
            ["superadmin default", ["superadmin", "default", "0"]],
            ["encrypted password unsafe", ["encrypted_password"]],
            ["internal tokens unsafe", ["reset_password_token", "confirmation_token", "unlock_token"]],
            ["malformed symbol", ["password_confirmationreset_password_token"]],
            ["safe custom fields", ["first_name", "last_name", "username", "phone", "cnic"]],
        ],
    },
    {
        "id": "auth_flow",
        "title": "Authentication flow diagnosis",
        "prompt": '''Rails 6.1 + Devise:
```ruby
Rails.application.routes.draw do
  root to: "products#index"
  devise_for :users, controllers: { sessions: 'users/sessions' }
end

class ApplicationController < ActionController::Base
  before_action :authenticate_user!
end

class ProductsController < ApplicationController
  def index; end
end

class Users::SessionsController < Devise::SessionsController
end
```
In at most 180 words: explain exactly what happens for an unauthenticated GET `/`; explain whether Devise controllers inherit the ApplicationController callback and why `authenticate_user!` does not block Devise's own sign-in actions; then show the smallest idiomatic change if only `ProductsController#index` should be public. Do not claim a 500 error unless the shown code actually causes one.''',
        "checks": [
            ["redirect to sign in", ["/users/sign_in"]],
            ["devise inherits app controller", ["inherit", "applicationcontroller"]],
            ["devise helper bypass", ["devise_controller?"]],
            ["public index fix", ["skip_before_action", "authenticate_user!"]],
        ],
    },
    {
        "id": "enum_mapping",
        "title": "Enum persistence reasoning",
        "prompt": '''Rails model and schema:
```ruby
class User < ApplicationRecord
  enum role: %w[ superadmin admin manager operator ]
  enum status: %w[ inactive active blocked archived ]
end
```
`role` and `status` are integer columns, both default 0.
In at most 180 words: give the exact integer mapping for every role and status; state the effective defaults; explain precisely why reordering either array later corrupts semantic interpretation of existing rows; and show safer explicit-hash enums preserving the current mappings.''',
        "checks": [
            ["role mapping", ["superadmin", "0", "operator", "3"]],
            ["status mapping", ["inactive", "0", "archived", "3"]],
            ["reorder warning", ["reorder"]],
            ["explicit hash", ["superadmin: 0", "admin: 1"]],
        ],
    },
    {
        "id": "crud_task",
        "title": "Idiomatic Rails CRUD implementation",
        "prompt": '''Rails 6.1 app already has a `products` table with non-null string `name`, root `products#index`, an empty Product model, and:
```ruby
class ProductsController < ApplicationController
  def index; end
end
```
Implement compact idiomatic CRUD for Product. Output only: (1) the routes addition while preserving the existing root, (2) Product model validation, and (3) ProductsController. Use strong params, one `before_action :set_product` for member actions, redirects on success, and `render ... status: :unprocessable_entity` on validation failure. Do not create a migration and do not add unrelated abstractions.''',
        "checks": [
            ["REST routes", ["resources :products"]],
            ["validation", ["validates :name", "presence: true"]],
            ["loader callback", ["before_action", "set_product"]],
            ["strong params", ["permit(:name)"]],
        ],
    },
]


def ask(prompt):
    payload = {
        "model": MODEL_SLUG,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 500,
        "stream": False,
    }
    req = urllib.request.Request(API, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    started = time.time()
    with urllib.request.urlopen(req, timeout=1200) as resp:
        data = json.load(resp)
    msg = data["choices"][0]["message"]
    content = msg.get("content") or msg.get("reasoning_content") or ""
    return content, round(time.time() - started, 2), data.get("timings", {}), msg


Path("benchmark-output").mkdir(exist_ok=True)
results = []
for test in TESTS:
    text, seconds, timings, raw_message = ask(test["prompt"])
    lower = text.lower()
    checks = []
    for label, terms in test["checks"]:
        passed = all(term.lower() in lower for term in terms)
        checks.append({"label": label, "passed": passed, "terms": terms})
    results.append({
        "id": test["id"], "title": test["title"], "seconds": seconds,
        "score": sum(c["passed"] for c in checks), "max_score": len(checks),
        "checks": checks, "timings": timings, "response": text,
        "raw_message": raw_message,
    })
    print(test["id"], results[-1]["score"], "/", len(checks), "seconds", seconds, flush=True)

summary = {
    "benchmark_version": 2,
    "model": MODEL_LABEL,
    "slug": MODEL_SLUG,
    "total": sum(r["score"] for r in results),
    "maximum": sum(r["max_score"] for r in results),
    "results": results,
}
Path("benchmark-output/results.json").write_text(json.dumps(summary, indent=2))
lines = [f"# {MODEL_LABEL} Rails benchmark v2", "", "Corpus: faheemKamboh/freshFruit (Rails 6.1)", f"Automated rubric: {summary['total']}/{summary['maximum']}", ""]
for r in results:
    lines += [f"## {r['title']}", f"Score: {r['score']}/{r['max_score']} — {r['seconds']}s", ""]
    for c in r["checks"]:
        lines.append(f"- {'PASS' if c['passed'] else 'MISS'}: {c['label']}")
    if r.get("timings"):
        lines += ["", "Timings: `" + json.dumps(r["timings"]) + "`"]
    lines += ["", "### Response", "", "```text", r["response"], "```", ""]
Path("benchmark-output/report.md").write_text("\n".join(lines))
print(f"TOTAL {summary['total']}/{summary['maximum']}", flush=True)
