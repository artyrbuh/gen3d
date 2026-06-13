#!/usr/bin/env python3
"""Tripo/fal asset pipeline CLI. Stdlib only — no pip install needed.

Subcommands (default `generate` — bare `--image/--out` invocations still work):

  generate      image -> 3D GLB (fal.ai Hunyuan/Tripo, or native Tripo API)
  import-model  upload an existing GLB/OBJ/FBX/STL to Tripo -> task id (FREE)
  check         pre-rig check on a task id -> riggable verdict (FREE)
  rig           rig a model for animation (25 credits ~= $0.25)
  animate       retarget preset animations onto a rigged model (10 credits each)
  balance       show remaining Tripo API credits

Generation backends (design notes: docs/pipeline-design.md):

  fal    fal.ai queue API, one FAL_KEY, two selectable models:
           --model hunyuan  fal-ai/hunyuan3d/v2          (~$0.05 plain, ~3x textured)
           --model tripo    tripo3d/tripo/v2.5/image-to-3d (Tripo's model, same key)
  tripo  Tripo's native developer API (api.tripo3d.ai), TRIPO_API_KEY,
         credits ~= $0.01 each, a generation ~= 20-40 credits.

The animation chain (docs/animation-chain.md):
  import-model --model hero.glb     -> import task id        (free)
  check --task-id <import-id>       -> riggable? rig_type?   (free)
  rig --task-id <import-id> --out rigged.glb                 (25 cr)
  animate --rig-task-id <rig-id> --presets run,jump --out anim.glb  (10 cr/preset)

Keys come from the environment, $GEN3D_ENV_FILE, a script-adjacent .env, or
~/.config/gen3d/env (KEY=value lines; .env files are gitignored).

Pass --json to any subcommand to get one machine-readable JSON result object on
stdout (human progress moves to stderr).

Examples:
  python3 gen3d.py --image concepts/toll_gate.png --out models/toll_gate.glb
  python3 gen3d.py import-model --model models/fantasy_hero.glb
  python3 gen3d.py check --task-id <id>
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(SCRIPT_DIR, ".env")
CONFIG_ENV_FILE = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "gen3d", "env")
# how the user invoked us — used in copy-pasteable "next step" hints
SELF = os.path.relpath(os.path.abspath(sys.argv[0])) if sys.argv[0] else "gen3d.py"
JSON_MODE = False

FAL_QUEUE = "https://queue.fal.run"
FAL_MODELS = {
    "hunyuan": "fal-ai/hunyuan3d/v2",
    "tripo": "tripo3d/tripo/v2.5/image-to-3d",
}
TRIPO_API = "https://api.tripo3d.ai/v2/openapi"

POLL_INTERVAL_S = 5
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # both providers cap uploads at 20 MB
MAX_MODEL_BYTES = 20 * 1024 * 1024  # STS upload cap

MIME_BY_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".webp": "image/webp"}
MODEL_FORMAT_BY_EXT = {".glb": "glb", ".obj": "obj", ".fbx": "fbx", ".stl": "stl"}

# Tripo retarget preset library (verified 2026-06-13, docs.tripo3d.ai animation/retarget)
PRESETS = ["idle", "walk", "run", "dive", "climb", "jump", "slash", "shoot",
           "hurt", "fall", "turn", "quadruped:walk", "hexapod:walk",
           "octopod:walk", "serpentine:march", "aquatic:march"]
RETARGET_CREDITS_PER_ANIM = 10
RIG_CREDITS = 25

KEY_HELP = {
    "FAL_KEY": "https://fal.ai/dashboard/keys",
    "TRIPO_API_KEY": "https://platform.tripo3d.ai (developer platform, separate from Studio)",
}


def fail(msg):
    print(f"gen3d: error: {msg}", file=sys.stderr)
    if JSON_MODE:
        print(json.dumps({"ok": False, "error": msg}))
    sys.exit(1)


def say(msg):
    """Progress line for humans — stderr in --json mode so stdout stays parseable."""
    print(msg, file=sys.stderr if JSON_MODE else sys.stdout)


def load_env_file():
    """Load keys from every env file in the chain. The real environment always
    wins (setdefault); files are read in priority order: $GEN3D_ENV_FILE, the
    script-adjacent .env, then ~/.config/gen3d/env."""
    explicit = os.environ.get("GEN3D_ENV_FILE")
    if explicit and not os.path.exists(explicit):
        fail(f"GEN3D_ENV_FILE points at a missing file: {explicit}")
    for path in (explicit, ENV_FILE, CONFIG_ENV_FILE):
        if not path or not os.path.exists(path):
            continue
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(),
                                      value.strip().strip('"').strip("'"))


def require_key(name):
    value = os.environ.get(name)
    if not value:
        fail(f"{name} is not set. Get one at {KEY_HELP[name]} and either export it, "
             f"or put '{name}=...' in $GEN3D_ENV_FILE, {ENV_FILE}, "
             f"or {CONFIG_ENV_FILE}")
    return value


def read_image(path):
    ext = os.path.splitext(path)[1].lower()
    if ext not in MIME_BY_EXT:
        fail(f"unsupported image type '{ext}' — use png/jpg/webp")
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        fail(f"cannot read image: {e}")
    if len(data) > MAX_IMAGE_BYTES:
        fail(f"image is {len(data) / 1e6:.1f} MB — provider limit is 20 MB")
    return data, MIME_BY_EXT[ext], ext.lstrip(".").replace("jpeg", "jpg")


def http_json(url, headers, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={**headers,
                                          **({"Content-Type": "application/json"} if data else {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        fail(f"HTTP {e.code} from {url}\n{e.read().decode(errors='replace')}")
    except urllib.error.URLError as e:
        fail(f"network error reaching {url}: {e.reason}")


def download(url, out_path, headers=None):
    tmp = out_path + ".part"
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp, open(tmp, "wb") as f:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
    except (urllib.error.URLError, OSError) as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        fail(f"download failed: {e}")
    os.replace(tmp, out_path)
    size = os.path.getsize(out_path)
    say(f"wrote {out_path} ({size / 1e6:.1f} MB)")
    return out_path, size


# ---------------------------------------------------------------- fal backend

def run_fal(args, image_data, mime):
    key = require_key("FAL_KEY")
    headers = {"Authorization": f"Key {key}"}
    model_id = FAL_MODELS[args.model]
    data_uri = f"data:{mime};base64,{base64.b64encode(image_data).decode()}"

    if args.model == "hunyuan":
        body = {"input_image_url": data_uri, "textured_mesh": args.texture}
        if args.max_faces:
            say("note: hunyuan3d/v2 has no face-limit parameter; --max-faces ignored "
                "(use --model tripo if the poly budget is strict)")
        cost = "~$0.05, ~3x if textured" if args.texture else "~$0.05"
    else:
        body = {"image_url": data_uri,
                "texture": "standard" if args.texture else "no",
                "pbr": True}
        if args.max_faces:
            body["face_limit"] = args.max_faces
        cost = "roughly $0.20-0.40"

    say(f"submitting to fal: {model_id} (cost {cost})")
    sub = http_json(f"{FAL_QUEUE}/{model_id}", headers, body)
    request_id = sub.get("request_id")
    status_url = sub.get("status_url")
    response_url = sub.get("response_url")
    if not (request_id and status_url and response_url):
        fail(f"unexpected submit response: {json.dumps(sub)[:500]}")
    say(f"task {request_id} queued")

    deadline = time.time() + args.timeout
    last = None
    while time.time() < deadline:
        st = http_json(status_url, headers)
        status = st.get("status")
        if status != last:
            say(f"  status: {status}")
            last = status
        if status == "COMPLETED":
            result = http_json(response_url, headers)
            mesh = result.get("model_mesh") or {}
            url = mesh.get("url")
            if not url:
                fail(f"no model_mesh.url in result: {json.dumps(result)[:500]}")
            _, size = download(url, args.out)
            return {"task_id": request_id, "output": args.out,
                    "output_bytes": size}
        if status in ("FAILED", "ERROR", "CANCELLED"):
            fail(f"fal task failed: {json.dumps(st)[:1000]}")
        time.sleep(POLL_INTERVAL_S)
    fail(f"timed out after {args.timeout}s waiting on fal task {request_id}")


# -------------------------------------------------------------- tripo backend

def tripo_headers():
    return {"Authorization": f"Bearer {require_key('TRIPO_API_KEY')}"}


def tripo_upload(headers, image_data, mime, path):
    boundary = uuid.uuid4().hex
    name = os.path.basename(path)
    body = (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n").encode() + image_data + \
           f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{TRIPO_API}/upload", data=body,
        headers={**headers, "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            out = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        fail(f"HTTP {e.code} from upload\n{e.read().decode(errors='replace')}")
    except urllib.error.URLError as e:
        fail(f"network error during upload: {e.reason}")
    token = (out.get("data") or {}).get("image_token")
    if not token:
        fail(f"no image_token in upload response: {json.dumps(out)[:500]}")
    return token


def tripo_create_task(headers, body):
    sub = http_json(f"{TRIPO_API}/task", headers, body)
    task_id = (sub.get("data") or {}).get("task_id")
    if not task_id:
        fail(f"no task_id in response: {json.dumps(sub)[:500]}")
    say(f"task {task_id} queued ({body['type']})")
    return task_id


def tripo_wait_task(headers, task_id, timeout):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        st = http_json(f"{TRIPO_API}/task/{task_id}", headers)
        data = st.get("data") or {}
        status = data.get("status")
        if status != last:
            say(f"  status: {status}")
            last = status
        if status == "success":
            return data
        if status in ("failed", "banned", "expired", "cancelled", "unknown"):
            fail(f"tripo task ended '{status}': {json.dumps(data)[:1000]}")
        time.sleep(POLL_INTERVAL_S)
    fail(f"timed out after {timeout}s waiting on tripo task {task_id}")


def tripo_download_model(data, out_path):
    output = data.get("output") or {}
    url = output.get("pbr_model") or output.get("model") or output.get("base_model")
    if not url:
        fail(f"no model url in output: {json.dumps(output)[:500]}")
    # download links expire ~5 minutes after success — grab it now
    return download(url, out_path)


def tripo_balance(headers):
    out = http_json(f"{TRIPO_API}/user/balance", headers)
    return out.get("data") or {}


def confirm_spend(headers, credits, what, assume_yes):
    bal = tripo_balance(headers)
    say(f"balance: {json.dumps(bal)}")
    say(f"about to spend ~{credits} credits (~${credits / 100:.2f}) on {what}")
    if assume_yes:
        return
    if not sys.stdin.isatty():
        fail("refusing to spend credits non-interactively — re-run with --yes "
             "after the user has approved the cost")
    say("proceed? [y/N] ")
    if input().strip().lower() not in ("y", "yes"):
        fail("aborted by user")


def run_tripo(args, image_data, mime, image_ext):
    headers = tripo_headers()

    say("uploading image to tripo...")
    token = tripo_upload(headers, image_data, mime, args.image)

    body = {"type": "image_to_model",
            "file": {"type": image_ext, "file_token": token},
            "texture": args.texture,
            "pbr": args.texture}
    if args.max_faces:
        body["face_limit"] = args.max_faces
    say("submitting image_to_model task (cost ~20-40 credits ~= $0.20-0.40)")
    task_id = tripo_create_task(headers, body)
    data = tripo_wait_task(headers, task_id, args.timeout)
    _, size = tripo_download_model(data, args.out)
    return {"task_id": task_id, "output": args.out, "output_bytes": size,
            "balance": tripo_balance(headers)}


# ----------------------------------------------------- STS upload (model files)

def _hmac_sha256(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def s3_put_sigv4(host, bucket, key, data, access_key, secret_key, session_token):
    """Minimal AWS SigV4 signer for a single path-style S3 PUT (stdlib only)."""
    # host is like s3.us-west-2.amazonaws.com — region is the second label
    parts = host.split(".")
    region = parts[1] if len(parts) > 3 and parts[0].startswith("s3") else "us-west-2"

    uri = "/" + urllib.parse.quote(f"{bucket}/{key}", safe="/-._~")
    payload_hash = hashlib.sha256(data).hexdigest()
    now = time.gmtime()
    amz_date = time.strftime("%Y%m%dT%H%M%SZ", now)
    datestamp = time.strftime("%Y%m%d", now)

    sign_headers = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "x-amz-security-token": session_token,
    }
    signed_list = ";".join(sorted(sign_headers))
    canonical_headers = "".join(f"{k}:{sign_headers[k]}\n" for k in sorted(sign_headers))
    canonical_request = "\n".join(
        ["PUT", uri, "", canonical_headers, signed_list, payload_hash])
    scope = f"{datestamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amz_date, scope,
         hashlib.sha256(canonical_request.encode()).hexdigest()])
    k = _hmac_sha256(("AWS4" + secret_key).encode(), datestamp)
    for part in (region, "s3", "aws4_request"):
        k = _hmac_sha256(k, part)
    signature = hmac.new(k, string_to_sign.encode(), hashlib.sha256).hexdigest()

    req = urllib.request.Request(
        f"https://{host}{uri}", data=data, method="PUT",
        headers={
            "Authorization": (f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
                              f"SignedHeaders={signed_list}, Signature={signature}"),
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_hash,
            "x-amz-security-token": session_token,
        })
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        fail(f"S3 PUT failed HTTP {e.code}\n{e.read().decode(errors='replace')[:1000]}")
    except urllib.error.URLError as e:
        fail(f"network error during S3 PUT: {e.reason}")


def sts_upload_model(headers, path):
    """Upload a model file via Tripo's STS flow; returns the task `file` dict."""
    ext = os.path.splitext(path)[1].lower()
    fmt = MODEL_FORMAT_BY_EXT.get(ext)
    if not fmt:
        fail(f"unsupported model type '{ext}' — use glb/obj/fbx/stl")
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        fail(f"cannot read model: {e}")
    if len(data) > MAX_MODEL_BYTES:
        fail(f"model is {len(data) / 1e6:.1f} MB — STS upload limit is 20 MB")

    say(f"requesting STS upload token ({fmt}, {len(data) / 1e6:.1f} MB)...")
    tok = http_json(f"{TRIPO_API}/upload/sts/token", headers, {"format": fmt})
    d = tok.get("data") or {}
    missing = [k for k in ("sts_ak", "sts_sk", "session_token",
                           "resource_bucket", "resource_uri") if not d.get(k)]
    if missing:
        fail(f"STS token response missing {missing}: {json.dumps(tok)[:500]}")
    host = d.get("s3_host") or "s3.us-west-2.amazonaws.com"

    say(f"uploading to s3://{d['resource_bucket']}/{d['resource_uri']} ...")
    s3_put_sigv4(host, d["resource_bucket"], d["resource_uri"], data,
                 d["sts_ak"], d["sts_sk"], d["session_token"])
    return {"type": fmt,
            "object": {"bucket": d["resource_bucket"], "key": d["resource_uri"]}}


# ------------------------------------------------------- animation subcommands

def cmd_import_model(args):
    headers = tripo_headers()
    file_spec = sts_upload_model(headers, args.model)
    say("submitting import_model task (FREE)")
    task_id = tripo_create_task(headers, {"type": "import_model", "file": file_spec})
    tripo_wait_task(headers, task_id, args.timeout)
    say(f"\nimport task id: {task_id}")
    say(f"next: python3 {SELF} check --task-id {task_id}")
    return {"ok": True, "command": "import-model", "task_id": task_id}


def cmd_check(args):
    headers = tripo_headers()
    say("submitting animate_prerigcheck task (FREE)")
    task_id = tripo_create_task(
        headers, {"type": "animate_prerigcheck",
                  "original_model_task_id": args.task_id})
    data = tripo_wait_task(headers, task_id, args.timeout)
    output = data.get("output") or {}
    say(f"\nriggable: {output.get('riggable')}")
    say(f"rig_type: {output.get('rig_type')}")
    if output.get("riggable"):
        say(f"next (costs {RIG_CREDITS} credits): python3 {SELF} rig "
            f"--task-id {args.task_id} --out <rigged.glb>")
    else:
        say(f"full output: {json.dumps(output)[:500]}")
    return {"ok": True, "command": "check", "model_task_id": args.task_id,
            "check_task_id": task_id, "riggable": output.get("riggable"),
            "rig_type": output.get("rig_type"), "raw_output": output}


def cmd_rig(args):
    headers = tripo_headers()
    if args.spec == "mixamo":
        say("warning: spec=mixamo skeletons CANNOT be retargeted by Tripo — "
            "only use this for an external Mixamo workflow")
    confirm_spend(headers, RIG_CREDITS, f"animate_rig ({args.rig_version})", args.yes)
    body = {"type": "animate_rig",
            "original_model_task_id": args.task_id,
            "model_version": args.rig_version,
            "out_format": args.out_format,
            "rig_type": args.rig_type,
            "spec": args.spec}
    task_id = tripo_create_task(headers, body)
    data = tripo_wait_task(headers, task_id, args.timeout)
    _, size = tripo_download_model(data, args.out)
    balance = tripo_balance(headers)
    say(f"balance after: {json.dumps(balance)}")
    say(f"\nrig task id: {task_id}")
    say(f"next ({RETARGET_CREDITS_PER_ANIM} credits per preset): python3 {SELF} "
        f"animate --rig-task-id {task_id} --presets run --out <anim.glb>")
    return {"ok": True, "command": "rig", "rig_task_id": task_id,
            "source_task_id": args.task_id, "output": args.out,
            "output_bytes": size, "credits_spent": RIG_CREDITS,
            "balance": balance}


def cmd_animate(args):
    headers = tripo_headers()
    presets = [p.strip().lower() for p in args.presets.split(",") if p.strip()]
    bad = [p for p in presets if p not in PRESETS]
    if bad:
        fail(f"unknown preset(s) {bad} — available: {', '.join(PRESETS)}")
    if len(presets) > 5:
        fail("at most 5 animations per retarget task")
    credits = RETARGET_CREDITS_PER_ANIM * len(presets)
    confirm_spend(headers, credits,
                  f"animate_retarget ({', '.join(presets)})", args.yes)
    body = {"type": "animate_retarget",
            "original_model_task_id": args.rig_task_id,
            "out_format": args.out_format,
            "bake_animation": True,
            "export_with_geometry": True}
    qualified = [f"preset:{p}" for p in presets]
    if len(qualified) == 1:
        body["animation"] = qualified[0]
    else:
        body["animations"] = qualified
    if args.in_place:
        body["animate_in_place"] = True
    task_id = tripo_create_task(headers, body)
    data = tripo_wait_task(headers, task_id, args.timeout)
    _, size = tripo_download_model(data, args.out)
    balance = tripo_balance(headers)
    say(f"balance after: {json.dumps(balance)}")
    say("done. next: audit the clips with a multi-angle probe before wiring "
        "(Godot: extras/godot/render_probe.gd with --anim)")
    return {"ok": True, "command": "animate", "task_id": task_id,
            "rig_task_id": args.rig_task_id, "presets": presets,
            "output": args.out, "output_bytes": size,
            "credits_spent": credits, "balance": balance}


def cmd_balance(_args):
    balance = tripo_balance(tripo_headers())
    say(json.dumps(balance, indent=2))
    return {"ok": True, "command": "balance", "balance": balance}


# ------------------------------------------------------------------------ cli

def cmd_generate(args):
    out_dir = os.path.dirname(args.out)
    if out_dir and not os.path.isdir(out_dir):
        fail(f"output directory does not exist: {out_dir}")

    image_data, mime, image_ext = read_image(args.image)

    if args.backend == "fal":
        result = run_fal(args, image_data, mime)
    else:
        result = run_tripo(args, image_data, mime, image_ext)

    say("done. next: audit the mesh from all angles before using it — see the "
        "generate-3d-asset skill (Godot: extras/godot/render_probe.gd)")
    return {"ok": True, "command": "generate", "backend": args.backend,
            "model": args.model if args.backend == "fal" else None, **result}


def add_timeout(p, default=600):
    p.add_argument("--timeout", type=int, default=default,
                   help=f"seconds to wait for the task (default {default})")


def add_json(p):
    p.add_argument("--json", action="store_true",
                   help="emit one machine-readable JSON result object on stdout "
                        "(progress lines move to stderr)")


def main():
    parser = argparse.ArgumentParser(
        description="Tripo/fal 3D asset pipeline: generation, import, rig, retarget.",
        epilog="Keys: FAL_KEY (fal backend) / TRIPO_API_KEY (tripo backend + animation), "
               f"from env, $GEN3D_ENV_FILE, {ENV_FILE}, or {CONFIG_ENV_FILE}")
    sub = parser.add_subparsers(dest="cmd")

    gen = sub.add_parser("generate", help="image -> 3D GLB (default subcommand)")
    gen.add_argument("--image", required=True, help="concept image (png/jpg/webp, <=20 MB)")
    gen.add_argument("--out", required=True, help="output GLB path, e.g. assets/models/foo.glb")
    gen.add_argument("--backend", choices=["fal", "tripo"], default="fal",
                     help="fal = fal.ai queue (default); tripo = native Tripo developer API")
    gen.add_argument("--model", choices=["hunyuan", "tripo"], default="hunyuan",
                     help="fal backend only: hunyuan3d/v2 (default) or Tripo v2.5 on fal")
    gen.add_argument("--texture", action=argparse.BooleanOptionalAction, default=True,
                     help="textured output (default on; --no-texture is cheaper)")
    gen.add_argument("--max-faces", type=int, default=20000,
                     help="polygon budget (default 20000 — mobile target; 0 = provider default)")
    add_timeout(gen)
    add_json(gen)
    gen.set_defaults(func=cmd_generate)

    imp = sub.add_parser("import-model",
                         help="upload a GLB/OBJ/FBX/STL to Tripo -> task id (FREE)")
    imp.add_argument("--model", required=True, help="model file (<=20 MB)")
    add_timeout(imp, 300)
    add_json(imp)
    imp.set_defaults(func=cmd_import_model)

    chk = sub.add_parser("check", help="pre-rig check: is this model riggable? (FREE)")
    chk.add_argument("--task-id", required=True,
                     help="task id of the model (e.g. from import-model)")
    add_timeout(chk, 300)
    add_json(chk)
    chk.set_defaults(func=cmd_check)

    rig = sub.add_parser("rig", help=f"rig a model for animation ({RIG_CREDITS} credits)")
    rig.add_argument("--task-id", required=True,
                     help="task id of the model to rig (e.g. from import-model)")
    rig.add_argument("--out", required=True, help="output path for the rigged model")
    rig.add_argument("--out-format", choices=["glb", "fbx"], default="glb")
    rig.add_argument("--rig-type", default="biped",
                     choices=["biped", "quadruped", "hexapod", "octopod", "avian",
                              "serpentine", "aquatic", "others"])
    rig.add_argument("--spec", choices=["tripo", "mixamo"], default="tripo",
                     help="tripo (default; required for Tripo retargeting) or mixamo")
    rig.add_argument("--rig-version", default="v2.5-20260210",
                     choices=["v2.5-20260210", "v1.0-20240301"],
                     help="rig model version (fall back to v1.0 if v2.5 rejects the source)")
    rig.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    add_timeout(rig)
    add_json(rig)
    rig.set_defaults(func=cmd_rig)

    ani = sub.add_parser("animate",
                         help=f"retarget presets onto a rigged model "
                              f"({RETARGET_CREDITS_PER_ANIM} credits each, max 5)")
    ani.add_argument("--rig-task-id", required=True, help="task id of the RIG task")
    ani.add_argument("--presets", required=True,
                     help=f"comma list from: {', '.join(PRESETS)}")
    ani.add_argument("--out", required=True, help="output path for the animated model")
    ani.add_argument("--out-format", choices=["glb", "fbx"], default="glb")
    ani.add_argument("--in-place", action="store_true",
                     help="animate in place (no root motion)")
    ani.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    add_timeout(ani)
    add_json(ani)
    ani.set_defaults(func=cmd_animate)

    bal = sub.add_parser("balance", help="show remaining Tripo API credits")
    add_json(bal)
    bal.set_defaults(func=cmd_balance)

    # back-compat: a bare `gen3d.py --image ... --out ...` means `generate`
    argv = sys.argv[1:]
    if argv and argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        argv = ["generate"] + argv
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        sys.exit(2)

    global JSON_MODE
    JSON_MODE = bool(getattr(args, "json", False))

    load_env_file()
    if getattr(args, "max_faces", None) == 0:
        args.max_faces = None

    result = args.func(args)
    if JSON_MODE and result is not None:
        print(json.dumps(result))


if __name__ == "__main__":
    main()
