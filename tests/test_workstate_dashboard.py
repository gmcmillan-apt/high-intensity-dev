import copy
import json
import runpy
import shutil
import sys
import time
import unittest
import uuid
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "tools" / "workstate-dashboard.py"
)
TOOLS_DIR = SCRIPT_PATH.parent


class WorkstateDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(TOOLS_DIR))
        cls.mod = runpy.run_path(str(SCRIPT_PATH))
        cls.globals = cls.mod["get_sessions_json"].__globals__

    def setUp(self):
        self.globals["sessions"].clear()
        self.globals["expired"].clear()
        self.original_warn_seconds = self.globals["WARN_SECONDS"]
        self.original_stale_seconds = self.globals["STALE_SECONDS"]
        self.original_service_cache = copy.deepcopy(self.globals["_service_status_cache"])
        self.original_transcript_cache = copy.deepcopy(self.globals["_TRANSCRIPT_SUMMARY_CACHE"])
        self.original_probe_http_service = self.globals["_probe_http_service"]
        self.original_probe_statuspage_service = self.globals["_probe_statuspage_service"]
        self.original_fetch_statuspage_summary = self.globals["_fetch_statuspage_summary"]
        self.original_json_loads = self.globals["json"].loads
        self.original_subprocess_run = self.globals["subprocess"].run
        self.original_scan_wt_tabs = self.globals["_scan_wt_tabs"]
        self.original_count_claude_processes = self.globals["_count_claude_processes"]
        self.original_get_transcript_summary = self.globals["_get_transcript_summary"]
        self.original_claude_projects_dir = self.globals["CLAUDE_PROJECTS_DIR"]
        self.original_system_boot_time = self.globals["SYSTEM_BOOT_TIME"]
        self.original_active_threshold = self.globals["ACTIVE_THRESHOLD_SEC"]
        self.original_idle_threshold = self.globals["IDLE_THRESHOLD_SEC"]

    def tearDown(self):
        self.globals["WARN_SECONDS"] = self.original_warn_seconds
        self.globals["STALE_SECONDS"] = self.original_stale_seconds
        self.globals["_service_status_cache"].clear()
        self.globals["_service_status_cache"].update(copy.deepcopy(self.original_service_cache))
        self.globals["_TRANSCRIPT_SUMMARY_CACHE"].clear()
        self.globals["_TRANSCRIPT_SUMMARY_CACHE"].update(
            copy.deepcopy(self.original_transcript_cache)
        )
        self.globals["_probe_http_service"] = self.original_probe_http_service
        self.globals["_probe_statuspage_service"] = self.original_probe_statuspage_service
        self.globals["_fetch_statuspage_summary"] = self.original_fetch_statuspage_summary
        self.globals["json"].loads = self.original_json_loads
        self.globals["subprocess"].run = self.original_subprocess_run
        self.globals["_scan_wt_tabs"] = self.original_scan_wt_tabs
        self.globals["_count_claude_processes"] = self.original_count_claude_processes
        self.globals["_get_transcript_summary"] = self.original_get_transcript_summary
        self.globals["CLAUDE_PROJECTS_DIR"] = self.original_claude_projects_dir
        self.globals["SYSTEM_BOOT_TIME"] = self.original_system_boot_time
        self.globals["ACTIVE_THRESHOLD_SEC"] = self.original_active_threshold
        self.globals["IDLE_THRESHOLD_SEC"] = self.original_idle_threshold

    def test_staleness_thresholds(self):
        now_iso = self.mod["now_iso"]()
        self.assertEqual(self.mod["staleness"](now_iso), "ok")

        self.globals["WARN_SECONDS"] = 1
        self.globals["STALE_SECONDS"] = 2
        self.assertEqual(self.mod["staleness"]("1970-01-01T00:00:00+00:00"), "idle")

    def test_session_lifecycle(self):
        upsert = self.mod["upsert_session"]

        body, code = upsert({"session_id": "s1", "name": "session-1", "task": "t1"})
        self.assertEqual(code, 200)
        self.assertEqual(body["active_sessions"], 1)
        self.assertIn("s1", self.globals["sessions"])

        body, code = upsert({"session_id": "s1", "task": "t2", "status": "Running"})
        self.assertEqual(code, 200)
        self.assertEqual(self.globals["sessions"]["s1"].task, "t2")
        self.assertEqual(self.globals["sessions"]["s1"].history[-1], "t1")

        body, code = upsert({"session_id": "s1", "status": "Done", "task": "done"})
        self.assertEqual(code, 200)
        self.assertNotIn("s1", self.globals["sessions"])
        self.assertGreaterEqual(len(self.globals["expired"]), 1)

    def test_template_file_present(self):
        template = Path(__file__).resolve().parents[1] / "tools" / "workstate-dashboard.template.html"
        self.assertTrue(template.exists())

    def test_thread_updates_refresh_parent_last_seen(self):
        upsert = self.mod["upsert_session"]
        upsert({"session_id": "parent", "name": "session-1", "task": "t1"})
        self.globals["sessions"]["parent"].last_seen = "1970-01-01T00:00:00+00:00"

        body, code = upsert(
            {
                "session_id": "child",
                "parent_id": "parent",
                "thread_id": "thread-1",
                "name": "worker",
                "task": "background task",
                "status": "Running",
            }
        )

        self.assertEqual(code, 200)
        self.assertEqual(body["thread_id"], "thread-1")
        self.assertIn("thread-1", self.globals["sessions"]["parent"].threads)
        self.assertNotEqual(
            self.globals["sessions"]["parent"].last_seen, "1970-01-01T00:00:00+00:00"
        )

    def test_get_sessions_json_includes_dashboard_and_service_cache(self):
        self.globals["_service_status_cache"]["data"] = {
            "items": [{"id": "frontend", "state": "online", "label": "Online"}],
            "summary": {"online": 1, "degraded": 0, "offline": 0},
            "updated_at": "2026-03-08T00:00:00+00:00",
        }

        payload = self.mod["get_sessions_json"]()

        self.assertEqual(payload["services"]["items"][0]["id"], "frontend")
        self.assertIn("service_groups", payload["dashboard"])
        self.assertIn("page_groups", payload["dashboard"])

    def test_get_service_statuses_uses_configured_groups(self):
        dashboard = self.globals["DASHBOARD_UI_CONFIG"]
        statuspage_components = {}
        for group in dashboard["service_groups"]:
            for service in group.get("services", []):
                if service.get("kind") == "statuspage_component":
                    statuspage_components[service["component_id"]] = {
                        "status": "operational",
                        "description": "ok",
                    }

        self.globals["_fetch_statuspage_summary"] = lambda timeout=10.0: statuspage_components
        self.globals["_probe_http_service"] = lambda service, timeout=5.0: {
            "id": service["id"],
            "name": service["name"],
            "display_url": service.get("display_url", ""),
            "link_url": service.get("link_url", ""),
            "state": "online",
            "label": "Online",
            "detail": "HTTP 200",
            "latency_ms": 12,
            "checked_at": "2026-03-08T00:00:00+00:00",
        }
        self.globals["_probe_statuspage_service"] = lambda service, components: {
            "id": service["id"],
            "name": service["name"],
            "display_url": service.get("display_url", ""),
            "link_url": service.get("link_url", ""),
            "state": "online",
            "label": "Operational",
            "detail": "ok",
            "latency_ms": None,
            "checked_at": "2026-03-08T00:00:00+00:00",
        }

        result = self.mod["_get_service_statuses"]()
        expected_count = sum(
            len(group.get("services", [])) for group in dashboard["service_groups"]
        )

        self.assertEqual(len(result["items"]), expected_count)
        self.assertEqual(result["summary"]["online"], expected_count)
        self.assertEqual(result["summary"]["degraded"], 0)
        self.assertEqual(result["summary"]["offline"], 0)

    def test_count_claude_processes_excludes_desktop_app(self):
        payload = [
            {
                "Id": 16216,
                "Path": (
                    "C:\\Program Files\\WindowsApps\\"
                    "Claude_1.1.4498.0_x64__pzs8sxrjxfjjc\\app\\Claude.exe"
                ),
            },
            {"Id": 35864, "Path": "C:\\Users\\gmcmillan\\.local\\bin\\claude.exe"},
            {"Id": 88088, "Path": None},
        ]

        def fake_run(*args, **kwargs):
            return type("Result", (), {"stdout": json.dumps(payload)})()

        self.globals["subprocess"].run = fake_run

        self.assertEqual(self.mod["_count_claude_processes"](), 1)

    def test_scan_claude_sessions_prefers_tab_backed_claude_code_sessions(self):
        tmpdir = Path(__file__).resolve().parent / f"tmp-transcript-{uuid.uuid4().hex}"
        project_dir = tmpdir / "C--Users-gmcmillan-Desktop-AI-Projects-ACV-AI-Agent-demo"
        project_dir.mkdir(parents=True)
        first = project_dir / f"{uuid.uuid4()}.jsonl"
        first.write_text("first\n", encoding="utf-8")
        time.sleep(1.1)
        second = project_dir / f"{uuid.uuid4()}.jsonl"
        second.write_text("second\n", encoding="utf-8")

        try:
            self.globals["CLAUDE_PROJECTS_DIR"] = tmpdir
            self.globals["SYSTEM_BOOT_TIME"] = 0
            self.globals["ACTIVE_THRESHOLD_SEC"] = 999999
            self.globals["IDLE_THRESHOLD_SEC"] = 999999
            self.globals["_count_claude_processes"] = lambda: 4
            self.globals["_scan_wt_tabs"] = lambda: [
                (123, second.stat().st_ctime, 0, "Claude Code")
            ]
            self.globals["_get_transcript_summary"] = lambda path: {
                "first_user_message": f"name-{path.stem}",
                "last_user_message": f"task-{path.stem}",
                "slug": "",
                "usage": {},
            }

            self.mod["scan_claude_sessions"]()

            auto_sessions = [
                session
                for session in self.globals["sessions"].values()
                if session.session_id.startswith(self.globals["AUTO_PREFIX"])
            ]
            self.assertEqual(len(auto_sessions), 1)
            self.assertIn("Claude Code", auto_sessions[0].tab)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_transcript_summary_cache_reuses_unchanged_file(self):
        lines = [
            json.dumps({"type": "user", "message": {"content": "first task"}}),
            json.dumps(
                {
                    "message": {
                        "model": "claude-sonnet-4-6",
                        "usage": {"input_tokens": 10, "output_tokens": 5},
                    }
                }
            ),
            json.dumps({"slug": "agent-slug"}),
            json.dumps(
                {
                    "type": "user",
                    "message": {"content": [{"type": "text", "text": "latest task"}]},
                }
            ),
        ]

        tmpdir = Path(__file__).resolve().parent / f"tmp-transcript-{uuid.uuid4().hex}"
        tmpdir.mkdir()
        try:
            path = tmpdir / "session.jsonl"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            calls = {"count": 0}

            def counted_loads(payload, *args, **kwargs):
                calls["count"] += 1
                return self.original_json_loads(payload, *args, **kwargs)

            self.globals["json"].loads = counted_loads

            first = self.mod["_get_transcript_summary"](path)
            count_after_first = calls["count"]
            second = self.mod["_get_transcript_summary"](path)

            self.assertEqual(first["first_user_message"], "first task")
            self.assertEqual(first["last_user_message"], "latest task")
            self.assertEqual(first["slug"], "agent-slug")
            self.assertEqual(first["usage"]["input_tokens"], 10)
            self.assertEqual(count_after_first, calls["count"])
            self.assertEqual(first, second)

            path.write_text(
                path.read_text(encoding="utf-8")
                + json.dumps({"type": "user", "message": {"content": "newest task"}})
                + "\n",
                encoding="utf-8",
            )
            updated = self.mod["_get_transcript_summary"](path)

            self.assertGreater(calls["count"], count_after_first)
            self.assertEqual(updated["last_user_message"], "newest task")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
