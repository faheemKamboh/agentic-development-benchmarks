import json
import time
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:8080/v1/chat/completions"
SYSTEM = "You are a senior Ruby on Rails engineer reviewing a real application. Be precise, identify concrete bugs from the supplied code, prefer idiomatic Rails fixes, and do not invent behavior not shown."

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
Review the concrete security/code problems. Include a corrected configure_permitted_parameters and call out dangerous role/default behavior.''',
        "checks": [
            ["superadmin default", ["superadmin", "default", "0"]],
            ["encrypted_password unsafe", ["encrypted_password"]],
            ["concatenated symbol typo", ["password_confirmationreset_password_token"]],
            ["safe custom fields", ["first_name", "last_name"]],
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
What happens to an unauthenticated request to `/`? Discuss callback inheritance for the Devise sessions controller and any sign-in redirect-loop/blocking risk. If products#index should be public but the rest of the app authenticated, give the smallest idiomatic fix.''',
        "checks": [
            ["root protected", ["authenticate_user", "products#index"]],
            ["devise callback inheritance", ["devise", "applicationcontroller"]],
            ["skip callback", ["skip_before_action"]],
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
Explain the exact persisted integer mapping for every value, what a new row defaults to, why reordering these arrays later is dangerous, and show safer explicit hash enums.''',
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
        "prompt": '''Rails 6.1 app currently has root `products#index`, an empty Product model, a products table with a non-null string `name`, and:
```ruby
class ProductsController < ApplicationController
  def index; end
end
```
Implement compact idiomatic CRUD for Product. Show routes, model validation, and controller. Use strong params, avoid repeated Product.find code, and correctly render validation failures.''',
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
        "model": "rails-specialist",
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        "temperature": 0.15,
        "max_tokens": 300,
        "stream": False,
    }
    req = urllib.request.Request(API, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    started = time.time()
    with urllib.request.urlopen(req, timeout=900) as resp:
        data = json.load(resp)
    return data["choices"][0]["message"]["content"], round(time.time() - started, 2), data.get("timings", {})


Path("benchmark-output").mkdir(exist_ok=True)
results = []
for test in TESTS:
    text, seconds, timings = ask(test["prompt"])
    lower = text.lower()
    checks = []
    for label, terms in test["checks"]:
        passed = all(term.lower() in lower for term in terms)
        checks.append({"label": label, "passed": passed, "terms": terms})
    results.append({
        "id": test["id"], "title": test["title"], "seconds": seconds,
        "score": sum(c["passed"] for c in checks), "max_score": len(checks),
        "checks": checks, "timings": timings, "response": text,
    })
    print(test["id"], results[-1]["score"], "/", len(checks), "seconds", seconds, flush=True)

Path("benchmark-output/results.json").write_text(json.dumps(results, indent=2))
total = sum(r["score"] for r in results)
maximum = sum(r["max_score"] for r in results)
lines = ["# Qwen3 8B Rails benchmark", "", "Corpus: faheemKamboh/freshFruit (Rails 6.1)", f"Automated rubric: {total}/{maximum}", ""]
for r in results:
    lines += [f"## {r['title']}", f"Score: {r['score']}/{r['max_score']} — {r['seconds']}s", ""]
    for c in r["checks"]:
        lines.append(f"- {'PASS' if c['passed'] else 'MISS'}: {c['label']}")
    if r.get("timings"):
        lines += ["", "Timings: `" + json.dumps(r["timings"]) + "`"]
    lines += ["", "### Response", "", "```text", r["response"], "```", ""]
Path("benchmark-output/report.md").write_text("\n".join(lines))
print(f"TOTAL {total}/{maximum}", flush=True)
