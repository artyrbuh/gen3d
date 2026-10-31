"""The Tripo paths in gen3d.py, driven through the real CLI against a local fake
of the V3 API. Never touches the network beyond 127.0.0.1 and spends nothing.

    python3 -m unittest discover -s tests

Response fixtures follow the examples in developers.tripo3d.ai/en/docs.
"""

import contextlib
import http.server
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gen3d  # noqa: E402

KEY = "tsk_test_secret_never_print_me"
TASK_ID = "123e4567-e89b-42d3-a456-426614174000"
GLB_BYTES = b"glTF-fake-model-bytes"


def created(task_id=TASK_ID):
    return 200, {"code": 0, "data": {"task_id": task_id}}


def task(status="success", task_type="image_to_model", output=None, **extra):
    data = {"task_id": TASK_ID, "type": task_type, "status": status,
            "progress": 100 if status == "success" else 0,
            "created_at": "2026-04-28T12:00:00Z"}
    if output is not None:
        data["output"] = output
    data.update(extra)
    return 200, {"code": 0, "data": data}


class FakeTripo:
    """Serves queued responses per (method, path) and records every request.
    The last response queued for a route repeats."""

    def __init__(self):
        self.routes = {}
        self.requests = []
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _serve(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                fake.requests.append({"method": self.command, "path": self.path,
                                      "headers": dict(self.headers), "raw": raw})
                queue = fake.routes.get((self.command, self.path))
                if not queue:
                    return self._send(404, {"code": 4001, "message": "No endpoint found"}, {})
                status, body, *rest = queue.pop(0) if len(queue) > 1 else queue[0]
                self._send(status, body, rest[0] if rest else {})

            def _send(self, status, body, headers):
                payload = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = do_PUT = _serve

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def on(self, method, path, *responses):
        self.routes[(method, path)] = list(responses)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def sent(self, method, path):
        return [r for r in self.requests if r["method"] == method and r["path"] == path]

    def body(self, path):
        hits = self.sent("POST", path)
        assert len(hits) == 1, f"expected one POST to {path}, got {len(hits)}"
        return json.loads(hits[0]["raw"])


class TripoV3Case(unittest.TestCase):
    def setUp(self):
        self.fake = FakeTripo()
        self.addCleanup(self.fake.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sleeps = []
        for patcher in (
            mock.patch.object(gen3d, "TRIPO_API", f"{self.fake.base}/v3"),
            mock.patch.object(gen3d, "ENV_FILE", os.path.join(self.tmp.name, "none")),
            mock.patch.object(gen3d, "CONFIG_ENV_FILE", os.path.join(self.tmp.name, "none")),
            mock.patch.dict(os.environ, {"TRIPO_API_KEY": KEY}),
            mock.patch.object(gen3d.time, "sleep", self.sleeps.append),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        os.environ.pop("GEN3D_ENV_FILE", None)
        self.fake.on("GET", "/v3/account/balance",
                     (200, {"code": 0, "data": {"balance": 1000.5, "frozen": 0.0}}))
        self.fake.on("POST", "/v3/files/presign",
                     (200, {"code": 0, "data": {
                         "presigned_url": f"{self.fake.base}/storage/input?X-Amz-Signature=s",
                         "file_token": "file_abc123", "expires_in": 1800}}))
        self.fake.on("PUT", "/storage/input?X-Amz-Signature=s", (200, b""))
        self.fake.on("GET", "/cdn/model.glb", (200, GLB_BYTES))
        self.model_url = f"{self.fake.base}/cdn/model.glb"

    def path(self, name, content=None):
        path = os.path.join(self.tmp.name, name)
        if content is not None:
            with open(path, "wb") as f:
                f.write(content)
        return path

    def run_cli(self, *argv):
        """Returns (exit_code, parsed_stdout_json_or_None, stderr). Every run also
        proves the key stayed out of both streams."""
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with mock.patch.object(sys, "argv", ["gen3d.py", *argv, "--json"]), \
                mock.patch.object(sys, "stdin", io.StringIO()), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                gen3d.main()
            except SystemExit as e:
                code = e.code
        self.assertNotIn(KEY, out.getvalue() + err.getvalue())
        text = out.getvalue().strip()
        return code, json.loads(text) if text else None, err.getvalue()

    def queue_generation(self, credits=30.0):
        self.fake.on("POST", "/v3/generation/image-to-model", created())
        self.fake.on("GET", f"/v3/tasks/{TASK_ID}", task(
            output={"model_url": self.model_url,
                    "rendered_image_url": f"{self.fake.base}/cdn/preview.png"},
            credits_consumed=credits))

    def generate(self, *extra, image="concept.png"):
        return self.run_cli("generate", "--backend", "tripo",
                            "--image", self.path(image, b"\x89PNG-bytes"),
                            "--out", self.path("out.glb"), *extra)


class Balance(TripoV3Case):
    def test_reports_decimal_balance_from_account_endpoint(self):
        code, result, _ = self.run_cli("balance")
        self.assertEqual(code, 0)
        self.assertEqual(result, {"ok": True, "command": "balance",
                                  "balance": {"balance": 1000.5, "frozen": 0.0}})
        request = self.fake.sent("GET", "/v3/account/balance")[0]
        self.assertEqual(request["headers"]["Authorization"], f"Bearer {KEY}")


class Generate(TripoV3Case):
    def test_textured_generation_end_to_end(self):
        self.queue_generation()
        code, result, _ = self.generate()
        self.assertEqual(code, 0)
        self.assertEqual(self.fake.body("/v3/files/presign"), {"format": "png"})
        self.assertEqual(self.fake.body("/v3/generation/image-to-model"), {
            "input": "file_abc123", "model": "v3.1-20260211",
            "texture": True, "pbr": True, "face_limit": 20000})
        with open(self.path("out.glb"), "rb") as f:
            self.assertEqual(f.read(), GLB_BYTES)
        self.assertEqual(result, {
            "ok": True, "command": "generate", "backend": "tripo", "model": None,
            "task_id": TASK_ID, "output": self.path("out.glb"),
            "output_bytes": len(GLB_BYTES), "credits_spent": 30.0,
            "balance": {"balance": 1000.5, "frozen": 0.0}})

    def test_upload_sends_image_bytes_without_the_api_key(self):
        self.queue_generation()
        self.generate()
        put = self.fake.sent("PUT", "/storage/input?X-Amz-Signature=s")[0]
        self.assertEqual(put["raw"], b"\x89PNG-bytes")
        self.assertEqual(put["headers"]["Content-Type"], "application/octet-stream")
        self.assertNotIn("Authorization", put["headers"])

    def test_download_does_not_send_the_api_key_to_the_cdn(self):
        self.queue_generation()
        self.generate()
        self.assertNotIn("Authorization",
                         self.fake.sent("GET", "/cdn/model.glb")[0]["headers"])

    def test_no_texture_turns_off_texture_and_pbr(self):
        self.queue_generation(20.0)
        self.generate("--no-texture", "--max-faces", "0")
        self.assertEqual(self.fake.body("/v3/generation/image-to-model"), {
            "input": "file_abc123", "model": "v3.1-20260211",
            "texture": False, "pbr": False})

    def test_webp_is_presigned_as_webp(self):
        self.queue_generation()
        self.generate(image="concept.webp")
        self.assertEqual(self.fake.body("/v3/files/presign"), {"format": "webp"})

    def test_estimate_used_when_task_omits_credits_consumed(self):
        self.fake.on("POST", "/v3/generation/image-to-model", created())
        self.fake.on("GET", f"/v3/tasks/{TASK_ID}", task(output={"model_url": self.model_url}))
        _, result, _ = self.generate()
        self.assertEqual(result["credits_spent"], 30)

    def test_success_without_model_url_names_the_task(self):
        self.fake.on("POST", "/v3/generation/image-to-model", created())
        self.fake.on("GET", f"/v3/tasks/{TASK_ID}", task(output={"other": "x"}))
        code, result, _ = self.generate()
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])
        self.assertIn(TASK_ID, result["error"])
        self.assertIn('"other"', result["error"])

    def test_failed_download_names_the_paid_task(self):
        self.fake.on("POST", "/v3/generation/image-to-model", created())
        self.fake.on("GET", "/cdn/gone.glb", (403, b"expired"))
        self.fake.on("GET", f"/v3/tasks/{TASK_ID}", task(
            output={"model_url": f"{self.fake.base}/cdn/gone.glb"}))
        code, result, _ = self.generate()
        self.assertEqual(code, 1)
        self.assertIn(TASK_ID, result["error"])

    def test_rejected_storage_put_stops_before_any_task(self):
        self.fake.on("PUT", "/storage/input?X-Amz-Signature=s", (403, b"<Error/>"))
        self.queue_generation()
        code, result, _ = self.generate()
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])
        self.assertEqual(self.fake.sent("POST", "/v3/generation/image-to-model"), [])


class ImportModel(TripoV3Case):
    def queue_import(self):
        self.fake.on("POST", "/v3/models/import", created())
        self.fake.on("GET", f"/v3/tasks/{TASK_ID}", task(task_type="import_model", output={
            "model_url": self.model_url, "normalize_scale": 0.365714,
            "normalize_translation": [-1, -0.5, -1.5]}))

    def test_uploads_and_imports_by_file_token(self):
        self.queue_import()
        code, result, _ = self.run_cli("import-model", "--model", self.path("hero.glb", GLB_BYTES))
        self.assertEqual(code, 0)
        self.assertEqual(self.fake.body("/v3/files/presign"), {"format": "glb"})
        self.assertEqual(self.fake.body("/v3/models/import"), {"input": "file_abc123"})
        self.assertEqual(result, {
            "ok": True, "command": "import-model", "task_id": TASK_ID,
            "file_token": "file_abc123", "normalize_scale": 0.365714,
            "normalize_translation": [-1, -0.5, -1.5]})

    def test_model_over_the_old_20_mb_sts_cap_is_accepted(self):
        self.queue_import()
        big = self.path("big.glb", b"\0" * (21 * 1024 * 1024))
        code, _, _ = self.run_cli("import-model", "--model", big)
        self.assertEqual(code, 0)

    def test_model_over_150_mb_is_refused_before_any_request(self):
        path = self.path("huge.glb")
        with open(path, "wb") as f:
            f.truncate(151 * 1024 * 1024)
        code, _, _ = self.run_cli("import-model", "--model", path)
        self.assertEqual(code, 1)
        self.assertEqual(self.fake.requests, [])

    def test_unsupported_extension_is_refused_before_any_request(self):
        code, result, _ = self.run_cli("import-model", "--model", self.path("a.blend", b"x"))
        self.assertEqual(code, 1)
        self.assertIn(".blend", result["error"])
        self.assertEqual(self.fake.requests, [])


class Check(TripoV3Case):
    def test_passes_input_and_returns_verdict(self):
        self.fake.on("POST", "/v3/animations/rig-check", created("check-task"))
        self.fake.on("GET", "/v3/tasks/check-task", task(
            task_type="animate_prerigcheck",
            output={"riggable": True, "rig_type": "quadruped"}, credits_consumed=0.0))
        code, result, _ = self.run_cli("check", "--task-id", "model-task")
        self.assertEqual(code, 0)
        self.assertEqual(self.fake.body("/v3/animations/rig-check"), {"input": "model-task"})
        self.assertEqual(result, {
            "ok": True, "command": "check", "model_task_id": "model-task",
            "check_task_id": "check-task", "riggable": True, "rig_type": "quadruped",
            "raw_output": {"riggable": True, "rig_type": "quadruped"}})


class Rig(TripoV3Case):
    def queue_rig(self):
        self.fake.on("POST", "/v3/animations/rig", created("rig-task"))
        self.fake.on("GET", "/v3/tasks/rig-task", task(
            task_type="animate_rig", output={"model_url": self.model_url},
            credits_consumed=25.0))

    def test_sends_v2_5_model_explicitly_because_the_v3_default_is_biped_only(self):
        self.queue_rig()
        code, result, _ = self.run_cli("rig", "--task-id", "model-task", "--rig-type", "quadruped",
                                       "--out", self.path("rigged.glb"), "--yes")
        self.assertEqual(code, 0)
        self.assertEqual(self.fake.body("/v3/animations/rig"), {
            "input": "model-task", "model": "v2.5-20260210", "out_format": "glb",
            "rig_type": "quadruped", "spec": "tripo"})
        self.assertEqual(result, {
            "ok": True, "command": "rig", "rig_task_id": "rig-task",
            "source_task_id": "model-task", "output": self.path("rigged.glb"),
            "output_bytes": len(GLB_BYTES), "credits_spent": 25.0,
            "balance": {"balance": 1000.5, "frozen": 0.0}})

    def test_v3_humanoid_rig_and_new_spec_are_accepted(self):
        self.queue_rig()
        code, _, _ = self.run_cli("rig", "--task-id", "m", "--rig-version", "v3.0-20260909",
                                  "--spec", "vrm", "--out", self.path("r.glb"), "--yes")
        self.assertEqual(code, 0)
        body = self.fake.body("/v3/animations/rig")
        self.assertEqual((body["model"], body["spec"]), ("v3.0-20260909", "vrm"))

    def test_refuses_to_spend_non_interactively_without_yes(self):
        self.queue_rig()
        code, result, _ = self.run_cli("rig", "--task-id", "m", "--out", self.path("r.glb"))
        self.assertEqual(code, 1)
        self.assertIn("--yes", result["error"])
        self.assertEqual(self.fake.sent("POST", "/v3/animations/rig"), [])


class Animate(TripoV3Case):
    def queue_animate(self):
        self.fake.on("POST", "/v3/animations/retarget", created("anim-task"))
        self.fake.on("GET", "/v3/tasks/anim-task", task(
            task_type="animate_retarget", output={"model_url": self.model_url},
            credits_consumed=20.0))

    def animate(self, presets, *extra):
        return self.run_cli("animate", "--rig-task-id", "rig-task", "--presets", presets,
                            "--out", self.path("anim.glb"), "--yes", *extra)

    def test_one_preset_uses_animation(self):
        self.queue_animate()
        self.animate("quadruped:walk")
        self.assertEqual(self.fake.body("/v3/animations/retarget"), {
            "input": "rig-task", "animation": "preset:quadruped:walk",
            "out_format": "glb", "export_with_geometry": True})

    def test_several_presets_use_animations_and_report_real_credits(self):
        self.queue_animate()
        code, result, _ = self.animate("run,jump", "--in-place")
        self.assertEqual(code, 0)
        self.assertEqual(self.fake.body("/v3/animations/retarget"), {
            "input": "rig-task", "animations": ["preset:run", "preset:jump"],
            "out_format": "glb", "export_with_geometry": True, "animate_in_place": True})
        self.assertEqual(result, {
            "ok": True, "command": "animate", "task_id": "anim-task",
            "rig_task_id": "rig-task", "presets": ["run", "jump"],
            "output": self.path("anim.glb"), "output_bytes": len(GLB_BYTES),
            "credits_spent": 20.0, "balance": {"balance": 1000.5, "frozen": 0.0}})

    def test_v1_rig_biped_preset_is_accepted(self):
        self.queue_animate()
        code, _, _ = self.animate("biped:dance_01")
        self.assertEqual(code, 0)
        self.assertEqual(self.fake.body("/v3/animations/retarget")["animation"],
                         "preset:biped:dance_01")

    def test_six_presets_are_refused_before_any_request(self):
        code, result, _ = self.animate("idle,walk,run,jump,fall,turn")
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])
        self.assertEqual(self.fake.requests, [])

    def test_unknown_preset_is_refused_before_any_request(self):
        code, result, _ = self.animate("moonwalk")
        self.assertEqual(code, 1)
        self.assertIn("moonwalk", result["error"])
        self.assertEqual(self.fake.requests, [])


class ErrorEnvelope(TripoV3Case):
    def assert_create_error(self, response, *expected):
        self.fake.on("POST", "/v3/animations/rig-check", response)
        code, result, _ = self.run_cli("check", "--task-id", "m")
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])
        for text in expected:
            self.assertIn(text, result["error"])

    def test_bad_key_shows_auth_failure(self):
        self.assert_create_error(
            (401, {"code": 1002, "message": "Authentication failed"}),
            "401", "1002", "Authentication failed")

    def test_insufficient_credit_shows_message_and_suggestion(self):
        self.assert_create_error(
            (403, {"code": 2010, "status": "error",
                   "message": "You don't have enough credit to create this task",
                   "suggestion": "Please purchase more credit", "request_id": "req_abc123"}),
            "2010", "enough credit", "purchase more credit", "req_abc123")

    def test_invalid_parameter_shows_code(self):
        self.assert_create_error(
            (400, {"code": 1004, "message": "One or more of your parameter is invalid"}),
            "1004", "parameter is invalid")

    def test_error_code_on_http_200_is_a_failure(self):
        self.assert_create_error((200, {"code": 1004, "message": "bad"}), "1004", "bad")

    def test_non_json_error_body_is_still_reported(self):
        self.assert_create_error((502, b"<html>Bad Gateway</html>"), "502", "Bad Gateway")

    def test_create_is_never_retried_on_server_error(self):
        self.assert_create_error((500, {"code": 1000, "message": "Unknown"}), "1000")
        self.assertEqual(len(self.fake.sent("POST", "/v3/animations/rig-check")), 1)

    def test_lost_create_response_warns_against_resubmitting(self):
        with mock.patch.object(gen3d.urllib.request, "urlopen",
                               side_effect=gen3d.urllib.error.URLError("reset")) as opened:
            code, result, _ = self.run_cli("check", "--task-id", "m")
        self.assertEqual(code, 1)
        self.assertEqual(opened.call_count, 1)
        self.assertIn("may have been created", result["error"])


class Polling(TripoV3Case):
    def setUp(self):
        super().setUp()
        self.fake.on("POST", "/v3/animations/rig-check", created())

    def check(self, *extra):
        return self.run_cli("check", "--task-id", "m", *extra)

    def done(self):
        return task(task_type="animate_prerigcheck",
                    output={"riggable": True, "rig_type": "biped"})

    def test_terminal_statuses_fail_with_the_task_error(self):
        for status in ("failed", "cancelled", "banned", "expired"):
            with self.subTest(status=status):
                self.fake.requests.clear()
                self.fake.on("GET", f"/v3/tasks/{TASK_ID}", task(
                    status, error_code=2008, error_message="rejected"))
                code, result, _ = self.check()
                self.assertEqual(code, 1)
                self.assertIn(status, result["error"])
                self.assertIn("rejected", result["error"])
                self.assertEqual(len(self.fake.sent("GET", f"/v3/tasks/{TASK_ID}")), 1)

    def test_unknown_status_keeps_polling(self):
        self.fake.on("GET", f"/v3/tasks/{TASK_ID}", task("queued"), task("unknown"),
                     task("running"), self.done())
        code, result, _ = self.check()
        self.assertEqual(code, 0)
        self.assertTrue(result["riggable"])

    def test_status_that_never_settles_times_out(self):
        self.fake.on("GET", f"/v3/tasks/{TASK_ID}", task("unknown"))
        code, result, _ = self.check("--timeout", "0")
        self.assertEqual(code, 1)
        self.assertIn("timed out", result["error"])
        self.assertIn(TASK_ID, result["error"])

    def test_polls_every_two_seconds(self):
        self.fake.on("GET", f"/v3/tasks/{TASK_ID}", task("running"), self.done())
        self.check()
        self.assertEqual(self.sleeps, [2])

    def test_rate_limit_while_polling_honours_retry_after(self):
        self.fake.on("GET", f"/v3/tasks/{TASK_ID}",
                     (429, {"code": 1007, "message": "Rate limit exceeded"}, {"Retry-After": "7"}),
                     self.done())
        code, _, _ = self.check()
        self.assertEqual(code, 0)
        self.assertEqual(self.sleeps, [7])

    def test_server_errors_while_polling_back_off_then_recover(self):
        err = (500, {"code": 1000, "message": "Unknown"})
        self.fake.on("GET", f"/v3/tasks/{TASK_ID}", err, err, self.done())
        code, _, _ = self.check()
        self.assertEqual(code, 0)
        self.assertEqual(self.sleeps, [1, 2])

    def test_gives_up_after_five_retries(self):
        self.fake.on("GET", f"/v3/tasks/{TASK_ID}", (500, {"code": 1000, "message": "Unknown"}))
        code, result, _ = self.check()
        self.assertEqual(code, 1)
        self.assertIn("1000", result["error"])
        self.assertEqual(len(self.fake.sent("GET", f"/v3/tasks/{TASK_ID}")), 6)

    def test_missing_task_fails_without_retrying(self):
        self.fake.on("GET", f"/v3/tasks/{TASK_ID}",
                     (404, {"code": 2001, "message": "The task is not found"}))
        code, result, _ = self.check()
        self.assertEqual(code, 1)
        self.assertIn("2001", result["error"])
        self.assertEqual(len(self.fake.sent("GET", f"/v3/tasks/{TASK_ID}")), 1)


if __name__ == "__main__":
    unittest.main()
