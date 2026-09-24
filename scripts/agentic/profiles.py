"""Profile resolution: which implementer backend/model and reviewer model are active."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import sys

from tasks import atomic_json, plain_path
from workflow import WorkflowError, configuration, run

ENV_NAME = "AGENTIC_PROFILE"
LOCAL_FILE = ".agentic-local/profile.json"
LEGACY_NAME = "legacy"
# Records that predate profiles ran Codex; their model was never recorded, so it stays unknown.
LEGACY_BACKEND = "codex"
FAMILIES = {"gpt": "openai", "claude": "anthropic"}
BACKEND_FAMILIES = {"codex": "openai", "claude": "anthropic"}
IMPLEMENTER_BACKENDS = ("codex", "claude")
MODEL_PATTERNS = {
    "codex": r"gpt-[a-z0-9.-]+",
    "claude": r"claude-[a-z0-9.-]+",
    "copilot": r"(?:gpt|claude)-[a-z0-9.-]+",
}
NAME_PATTERN = r"[a-z0-9][a-z0-9-]{0,39}"
SETTING_SOURCES = ("user", "project", "local")
IMPLEMENTER_KEYS = {
    "codex": {"backend", "model"},
    "claude": {
        "backend",
        "model",
        "permission_policy",
        "allowed_tools_extra",
        "disallowed_tools_extra",
        "setting_sources",
        "sandbox",
        "max_budget_usd",
    },
}
REVIEWER_KEYS = {"backend", "model", "max_ai_credits", "timeout_seconds"}


def warn(message):
    print(f"warning: {message}", file=sys.stderr)


def note(message):
    print(f"note: {message}", file=sys.stderr)


def family(model):
    prefix = str(model).split("-", 1)[0]
    if prefix not in FAMILIES:
        raise WorkflowError(f"Model ID {model!r} must start with a known family prefix (gpt-, claude-)")
    return FAMILIES[prefix]


def validate_model(backend, model):
    if not isinstance(model, str) or not re.fullmatch(MODEL_PATTERNS[backend], model):
        raise WorkflowError(
            f"{backend} model must be an explicit ID matching {MODEL_PATTERNS[backend]}, not {model!r}; "
            "aliases and auto are refused because they float"
        )
    return model


def tool_rules(value, label):
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\(.*\))?", item)
        for item in value
    ):
        raise WorkflowError(f"{label} must be a list of tool permission rules such as Bash(npm test *)")
    return list(value)


def normalize_implementer(name, spec):
    if not isinstance(spec, dict):
        raise WorkflowError(f"Profile {name!r} needs an implementer object")
    backend = spec.get("backend")
    if backend not in IMPLEMENTER_BACKENDS:
        raise WorkflowError(f"Profile {name!r}: implementer backend must be codex or claude, not {backend!r}")
    unknown = set(spec) - IMPLEMENTER_KEYS[backend]
    if unknown:
        raise WorkflowError(
            f"Profile {name!r}: unknown implementer keys for {backend}: {', '.join(sorted(unknown))}"
        )
    model = validate_model(backend, spec.get("model"))
    result = {"backend": backend, "model": model, "family": family(model)}
    if backend == "codex":
        return result
    policy = spec.get("permission_policy", "restricted")
    if policy == "bypass":
        raise WorkflowError(
            "Bypass containment is a per-launch operator decision (launch --containment bypass), "
            "not a configured default"
        )
    if policy != "restricted":
        raise WorkflowError(f"Profile {name!r}: permission_policy must be restricted, not {policy!r}")
    sources = spec.get("setting_sources", ["user", "project"])
    if (
        not isinstance(sources, list)
        or not sources
        or any(item not in SETTING_SOURCES for item in sources)
        or len(set(sources)) != len(sources)
    ):
        raise WorkflowError(
            f"Profile {name!r}: setting_sources must be a nonempty subset of user, project, local"
        )
    sandbox = spec.get("sandbox")
    if sandbox is not None and not isinstance(sandbox, dict):
        raise WorkflowError(f"Profile {name!r}: sandbox must be null or a settings object")
    budget = spec.get("max_budget_usd")
    if budget is not None and (
        isinstance(budget, bool) or not isinstance(budget, int | float) or budget <= 0
    ):
        raise WorkflowError(f"Profile {name!r}: max_budget_usd must be null or a positive number")
    result.update(
        permission_policy="restricted",
        allowed_tools_extra=tool_rules(spec.get("allowed_tools_extra", []), "allowed_tools_extra"),
        disallowed_tools_extra=tool_rules(spec.get("disallowed_tools_extra", []), "disallowed_tools_extra"),
        setting_sources=list(sources),
        sandbox=sandbox,
        max_budget_usd=budget,
    )
    return result


def positive_int(value, label):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise WorkflowError(f"{label} must be null or a positive integer")
    return value


def normalize_reviewer(name, spec):
    if not isinstance(spec, dict):
        raise WorkflowError(f"Profile {name!r} needs a reviewer object")
    if spec.get("backend") != "copilot":
        raise WorkflowError(f"Profile {name!r}: reviewer backend must be copilot")
    unknown = set(spec) - REVIEWER_KEYS
    if unknown:
        raise WorkflowError(f"Profile {name!r}: unknown reviewer keys: {', '.join(sorted(unknown))}")
    model = validate_model("copilot", spec.get("model"))
    return {
        "backend": "copilot",
        "model": model,
        "family": family(model),
        "max_ai_credits": positive_int(spec.get("max_ai_credits"), "reviewer max_ai_credits"),
        "timeout_seconds": positive_int(spec.get("timeout_seconds"), "reviewer timeout_seconds"),
    }


def load_profiles(cfg):
    version = cfg.get("schema_version")
    if version == 1:
        if "profiles" in cfg or "default_profile" in cfg:
            raise WorkflowError("Declare profiles with schema_version 2")
        if "openai_model" not in cfg or "copilot_model" not in cfg:
            raise WorkflowError(
                "Schema 1 configuration lacks openai_model/copilot_model; migrate to schema 2 profiles"
            )
        specs = {
            LEGACY_NAME: {
                "implementer": {"backend": "codex", "model": cfg["openai_model"]},
                "reviewer": {"backend": "copilot", "model": cfg["copilot_model"]},
            }
        }
        default = LEGACY_NAME
    elif version == 2:
        if "openai_model" in cfg or "copilot_model" in cfg:
            raise WorkflowError("Schema 2 declares models inside profiles; remove openai_model/copilot_model")
        specs = cfg.get("profiles")
        if not isinstance(specs, dict) or not specs:
            raise WorkflowError("Schema 2 configuration needs a nonempty profiles object")
        default = cfg.get("default_profile")
        if default not in specs:
            raise WorkflowError("default_profile must name a declared profile")
    else:
        raise WorkflowError("Unsupported .agentic/config.json schema (expected 1 or 2)")
    profiles = {}
    for name, spec in specs.items():
        if not isinstance(name, str) or not re.fullmatch(NAME_PATTERN, name):
            raise WorkflowError(f"Profile name {name!r} must be 1-40 lowercase letters, digits or hyphens")
        if not isinstance(spec, dict) or set(spec) != {"implementer", "reviewer"}:
            raise WorkflowError(f"Profile {name!r} must declare exactly implementer and reviewer")
        implementer = normalize_implementer(name, spec["implementer"])
        reviewer = normalize_reviewer(name, spec["reviewer"])
        profiles[name] = {
            "name": name,
            "implementer": implementer,
            "reviewer": reviewer,
            "same_family": implementer["family"] == reviewer["family"],
        }
    return {"default_profile": default, "profiles": profiles}


def private_root(repo):
    ignored = run(["git", "-C", repo.main, "check-ignore", ".agentic-local/probe"], check=False)
    tracked = run(["git", "-C", repo.main, "ls-files", ".agentic-local"]).stdout
    if ignored.returncode or tracked.strip():
        raise WorkflowError("Control .agentic-local must be ignored and untracked")
    return plain_path(repo.main / ".agentic-local")


def local_path(repo):
    return plain_path(private_root(repo) / "profile.json")


def local_selection(repo):
    path = local_path(repo)
    if not path.exists():
        return None
    if not path.is_file():
        raise WorkflowError("Local profile selection is not a regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise WorkflowError("Local profile selection is malformed; run `workflow.py profile clear`") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or not isinstance(value.get("profile"), str)
        or not isinstance(value.get("allow_same_family"), bool)
    ):
        raise WorkflowError("Local profile selection is malformed; run `workflow.py profile clear`")
    return value


def same_family_message(profile):
    implementer, reviewer = profile["implementer"], profile["reviewer"]
    return (
        f"Profile {profile['name']!r} pairs same-family models ({implementer['family']}: implementer "
        f"{implementer['model']}, reviewer {reviewer['model']}); record the exception with: "
        f"workflow.py profile use {profile['name']} --allow-same-family --reason TEXT"
    )


def active_profile(repo, cfg=None, *, warn_same_family=True):
    cfg = configuration(repo.root) if cfg is None else cfg
    declared = load_profiles(cfg)
    env_name = os.environ.get(ENV_NAME, "").strip()
    local = local_selection(repo)
    if env_name:
        name, source = env_name, "env"
    elif local:
        name, source = local["profile"], "local"
    else:
        name, source = declared["default_profile"], "default"
    if name not in declared["profiles"]:
        origin = {"env": ENV_NAME, "local": LOCAL_FILE, "default": "default_profile"}[source]
        raise WorkflowError(
            f"Profile {name!r} (from {origin}) is not declared in .agentic/config.json "
            f"(declared: {', '.join(sorted(declared['profiles']))})"
        )
    profile = dict(declared["profiles"][name])
    profile["source"] = source
    profile["allow_same_family"] = bool(local and local["profile"] == name and local["allow_same_family"])
    if profile["same_family"]:
        if not profile["allow_same_family"]:
            raise WorkflowError(same_family_message(profile))
        if warn_same_family:
            warn(
                f"profile {name!r} uses same-family implementer and reviewer ({profile['implementer']['family']}); "
                f"independent review diversity is reduced (recorded reason: {local.get('reason') or 'none'})"
            )
    return profile


def use_profile(repo, name, allow_same_family=False, reason=None):
    if os.environ.get("AGENTIC_EXECUTOR_ROLE"):
        raise WorkflowError("Managed executors must not change the active profile")
    declared = load_profiles(configuration(repo.root))
    if name not in declared["profiles"]:
        raise WorkflowError(
            f"Profile {name!r} is not declared in .agentic/config.json "
            f"(declared: {', '.join(sorted(declared['profiles']))})"
        )
    profile = declared["profiles"][name]
    if profile["same_family"] and not allow_same_family:
        raise WorkflowError(same_family_message(profile))
    if reason is not None and (not reason.strip() or len(reason) > 2000):
        raise WorkflowError("A recorded reason must be 1-2000 characters")
    record = {
        "schema_version": 1,
        "profile": name,
        "allow_same_family": bool(allow_same_family),
        "reason": reason,
        "recorded_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    atomic_json(local_path(repo), record)
    return record


def clear_profile(repo):
    if os.environ.get("AGENTIC_EXECUTOR_ROLE"):
        raise WorkflowError("Managed executors must not change the active profile")
    path = local_path(repo)
    existed = path.exists()
    if existed:
        path.unlink()
    return {"cleared": existed, "default_profile": load_profiles(configuration(repo.root))["default_profile"]}


def pinned_executor(executor):
    """The backend (and model, when recorded) a task record is bound to.

    Records that predate profiles ran Codex, but their model was never recorded, so the
    pin carries ``model: None`` and only the backend participates in comparisons.
    """
    if not isinstance(executor, dict):
        raise WorkflowError("Executor record is invalid")
    if "backend" not in executor:
        return {
            "backend": LEGACY_BACKEND,
            "model": None,
            "family": BACKEND_FAMILIES[LEGACY_BACKEND],
            "profile": None,
            "legacy": True,
        }
    backend, model = executor.get("backend"), executor.get("model")
    if backend not in IMPLEMENTER_BACKENDS:
        raise WorkflowError(f"Executor record names an unsupported backend {backend!r}")
    validate_model(backend, model)
    return {
        "backend": backend,
        "model": model,
        "family": family(model),
        "profile": executor.get("profile"),
        "legacy": False,
    }


def show(repo, cfg=None):
    profile = active_profile(repo, cfg)
    local = local_selection(repo)
    return {
        **profile,
        "local_file": str(local_path(repo)) if local else None,
        "local_selection": local,
        "tools": {tool: shutil.which(tool) for tool in ("codex", "claude", "copilot")},
    }


def listing(repo, cfg=None):
    cfg = configuration(repo.root) if cfg is None else cfg
    declared = load_profiles(cfg)
    try:
        active, error = active_profile(repo, cfg, warn_same_family=False)["name"], None
    except WorkflowError as exc:
        active, error = None, str(exc)
    return {
        "default_profile": declared["default_profile"],
        "active": active,
        "active_error": error,
        "profiles": {
            name: {
                "implementer": {"backend": p["implementer"]["backend"], "model": p["implementer"]["model"]},
                "reviewer": {"backend": "copilot", "model": p["reviewer"]["model"]},
                "same_family": p["same_family"],
            }
            for name, p in declared["profiles"].items()
        },
    }


def add_commands(sub):
    parser = sub.add_parser(
        "profile", help="Show, list, select or clear the active implementer/reviewer profile"
    )
    actions = parser.add_subparsers(dest="profile_command", required=True)
    actions.add_parser("show")
    actions.add_parser("list")
    use = actions.add_parser("use")
    use.add_argument("name")
    use.add_argument("--allow-same-family", action="store_true")
    use.add_argument("--reason")
    actions.add_parser("clear")


def dispatch(repo, args):
    if args.profile_command == "show":
        return show(repo)
    if args.profile_command == "list":
        return listing(repo)
    if args.profile_command == "use":
        return use_profile(repo, args.name, args.allow_same_family, args.reason)
    if args.profile_command == "clear":
        return clear_profile(repo)
    raise WorkflowError("Unknown profile operation")
