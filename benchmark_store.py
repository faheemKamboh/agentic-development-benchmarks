import os
from datetime import datetime, timezone


class BenchmarkStore:
    """Best-effort durable benchmark storage. Local artifacts remain authoritative fallback."""

    def __init__(self, github_run_id, model_slug, model_label, benchmark_version="active-v5", target="freshfruit-sanitized"):
        self.url = os.environ.get("NEON_DATABASE_URL", "").strip()
        self.github_run_id = github_run_id
        self.model_slug = model_slug
        self.model_label = model_label
        self.benchmark_version = benchmark_version
        self.target = target
        self.run_id = f"gh-{github_run_id}" if github_run_id else f"local-{int(datetime.now(timezone.utc).timestamp())}"
        self.conn = None
        self.session_id = None
        self.seq = 0
        self.last_error = None
        self._psycopg = None
        self._Jsonb = None

        if not self.url:
            return

        try:
            import psycopg
            from psycopg.types.json import Jsonb

            self._psycopg = psycopg
            self._Jsonb = Jsonb
            self.conn = psycopg.connect(self.url, autocommit=True, connect_timeout=10)
            self._start_session()
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._disable()

    @property
    def enabled(self):
        return self.conn is not None and self.session_id is not None

    def _disable(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
        self.conn = None
        self.session_id = None

    def _start_session(self):
        metadata = self._Jsonb({
            "source": "github-actions",
            "storage": "incremental",
            "model_label": self.model_label,
        })
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO benchmark_runs
                    (id, github_run_id, benchmark_version, target, started_at, metadata)
                VALUES (%s, %s, %s, %s, now(), %s)
                ON CONFLICT (id) DO UPDATE SET
                    github_run_id = EXCLUDED.github_run_id,
                    benchmark_version = EXCLUDED.benchmark_version,
                    target = EXCLUDED.target,
                    metadata = benchmark_runs.metadata || EXCLUDED.metadata
                """,
                (self.run_id, int(self.github_run_id) if self.github_run_id else None,
                 self.benchmark_version, self.target, metadata),
            )
            cur.execute(
                """
                INSERT INTO model_sessions
                    (run_id, model_slug, model_label, status, started_at)
                VALUES (%s, %s, %s, 'running', now())
                ON CONFLICT (run_id, model_slug) DO UPDATE SET
                    model_label = EXCLUDED.model_label,
                    status = 'running',
                    started_at = now(),
                    completed_at = NULL,
                    final_report = NULL
                RETURNING id
                """,
                (self.run_id, self.model_slug, self.model_label),
            )
            self.session_id = cur.fetchone()[0]
            # A GitHub rerun reuses the same (run, model) key. Clear only that session's stale trace.
            cur.execute("DELETE FROM agent_events WHERE session_id = %s", (self.session_id,))
            cur.execute("DELETE FROM benchmark_findings WHERE session_id = %s", (self.session_id,))

    def persist(self, event):
        if not self.enabled:
            return False
        try:
            self.seq += 1
            payload = self._Jsonb(event)
            event_type = str(event.get("type", "event"))
            with self.conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO agent_events (session_id, seq, event_type, payload) VALUES (%s, %s, %s, %s)",
                    (self.session_id, self.seq, event_type, payload),
                )
                if event_type == "finding" and isinstance(event.get("finding"), dict):
                    finding = event["finding"]
                    refs = [n for n in (finding.get("evidence_steps") or []) if isinstance(n, int)]
                    cur.execute(
                        """
                        INSERT INTO benchmark_findings
                            (session_id, title, severity, claim, evidence_steps, fix,
                             mechanically_evidenced, raw)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            self.session_id,
                            finding.get("title", "Untitled"),
                            finding.get("severity"),
                            finding.get("claim"),
                            refs,
                            finding.get("fix"),
                            bool(finding.get("mechanically_evidenced")),
                            self._Jsonb(event),
                        ),
                    )
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._disable()
            return False

    def finish(self, final_report, status="completed"):
        if not self.enabled:
            return False
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE model_sessions
                    SET status = %s, completed_at = now(), final_report = %s
                    WHERE id = %s
                    """,
                    (status, self._Jsonb(final_report), self.session_id),
                )
                cur.execute(
                    """
                    UPDATE benchmark_runs r
                    SET completed_at = now()
                    WHERE r.id = %s
                      AND NOT EXISTS (
                          SELECT 1 FROM model_sessions ms
                          WHERE ms.run_id = r.id AND ms.status = 'running'
                      )
                    """,
                    (self.run_id,),
                )
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._disable()
            return False

    def diagnostic(self):
        return {
            "configured": bool(self.url),
            "enabled": self.enabled,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "events_written": self.seq,
            "error": self.last_error,
        }
