"""Profile resolution: which implementer backend/model and reviewer policy are active."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import sys
from pathlib import Path

import review_policy
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
REVIEWER_BACKENDS = ("copilot", "claude-code")
MODEL_PATTERNS = {
    "codex": r"gpt-[a-z0-9.-]+",
    "claude": r"claude-[a-z0-9.-]+",
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
REVIEWER_KEYS = {
    "copilot": {"backend", "model", "effort", "max_ai_credits", "timeout_seconds", "cli_version", "adapter"},
    "claude-code": {
        "backend",
        "model",
        "effort",
        "max_estimated_usd",
        "timeout_seconds",
        "login_root",
        "billing_mode",
        "cli_version",
        "adapter",
    },
}
SUPPORTED_SCHEMAS = (1, 2, 3)
_NOTED_MIGRATIONS = set()


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
    if backend not in MODEL_PATTERNS:
        raise WorkflowError(f"Unsupported implementer backend {backend!r}")
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


def absolute_path(value, label):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise WorkflowError(f"{label} must be null or an absolute path")
    path = Path(value).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise WorkflowError(f"{label} must be an absolute path without parent components")
    return str(path)


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


def normalize_reviewer(name, spec, cfg, *, allow_claude_code=True):
    if not isinstance(spec, dict):
        raise WorkflowError(f"Profile {name!r} needs a reviewer object")
    backend = spec.get("backend")
    if backend not in REVIEWER_BACKENDS:
        raise WorkflowError(
            f"Profile {name!r}: reviewer backend must be copilot or claude-code, not {backend!r}"
        )
    if backend == "claude-code" and not allow_claude_code:
        raise WorkflowError(f"Profile {name!r}: reviewer backend claude-code requires schema_version 3")
    unknown = set(spec) - REVIEWER_KEYS[backend]
    if unknown:
        raise WorkflowError(
            f"Profile {name!r}: unknown reviewer keys for {backend}: {', '.join(sorted(unknown))}"
        )
    if not isinstance(spec.get("model"), str):
        raise WorkflowError(
            f"Profile {name!r}: reviewer model must be an explicit exact model ID, never defaulted"
        )
    try:
        selected = review_policy.choices(backend, spec.get("model"), spec.get("effort"), cfg=cfg)
    except WorkflowError as exc:
        raise WorkflowError(f"Profile {name!r}: {exc}") from None
    provider = review_policy.PROVIDERS[backend]
    for key, expected in (("cli_version", provider["cli"]["version"]), ("adapter", provider["adapter"])):
        if key in spec and spec[key] != expected:
            raise WorkflowError(
                f"Profile {name!r} pins reviewer {key} {spec[key]!r}, but this harness ships {expected!r}"
            )
    result = {
        "backend": backend,
        "model": selected["model"],
        "effort": selected["effort"],
        "family": family(selected["model"]),
        "timeout_seconds": positive_int(
            spec.get("timeout_seconds"), f"Profile {name!r} reviewer timeout_seconds"
        ),
    }
    if result["timeout_seconds"] is not None and result["timeout_seconds"] > 900:
        raise WorkflowError(f"Profile {name!r}: reviewer timeout_seconds must be at most 900")
    if backend == "copilot":
        result["max_ai_credits"] = positive_int(
            spec.get("max_ai_credits"), f"Profile {name!r} reviewer max_ai_credits"
        )
        return result
    estimate = spec.get("max_estimated_usd")
    if estimate is not None and (
        isinstance(estimate, bool) or not isinstance(estimate, int | float) or not 0 < estimate <= 10
    ):
        raise WorkflowError(f"Profile {name!r}: reviewer max_estimated_usd must be null or in (0, 10]")
    billing = spec.get("billing_mode", provider["billing_mode"])
    if billing != provider["billing_mode"]:
        raise WorkflowError(f"Profile {name!r}: reviewer billing_mode must be {provider['billing_mode']!r}")
    result.update(
        max_estimated_usd=estimate,
        login_root=absolute_path(spec.get("login_root"), f"Profile {name!r} reviewer login_root"),
        billing_mode=billing,
    )
    return result


def shimmed_specs(cfg):
    """Derive profile specifications from older configuration dialects without rewriting them."""
    version = cfg.get("schema_version")
    has_profiles = "profiles" in cfg or "default_profile" in cfg
    has_models = "openai_model" in cfg or "copilot_model" in cfg
    has_review = any(key in cfg for key in ("review_provider", "review_model", "review_effort"))
    if version == 1:
        if has_profiles or has_review:
            raise WorkflowError("Declare profiles with schema_version 3")
        if "openai_model" not in cfg or "copilot_model" not in cfg:
            raise WorkflowError(
                "Schema 1 configuration lacks openai_model/copilot_model; migrate to schema 3 profiles"
            )
        specs = {
            LEGACY_NAME: {
                "implementer": {"backend": "codex", "model": cfg["openai_model"]},
                "reviewer": {"backend": "copilot", "model": cfg["copilot_model"], "effort": "default"},
            }
        }
        return specs, LEGACY_NAME, None, True, False
    if version == 2:
        if has_profiles:
            if has_models or has_review:
                raise WorkflowError(
                    "Schema 2 declares models inside profiles; remove openai_model/copilot_model"
                )
            return cfg.get("profiles"), cfg.get("default_profile"), cfg.get("hosted_profile"), True, False
        if "openai_model" not in cfg or "copilot_model" not in cfg:
            raise WorkflowError("Schema 2 configuration needs profiles, or openai_model/copilot_model")
        provider = cfg.get("review_provider", "claude-code")
        if provider not in REVIEWER_BACKENDS:
            raise WorkflowError(f"Unsupported review_provider {provider!r}")
        copilot_model = cfg.get("review_model") if provider == "copilot" else cfg["copilot_model"]
        specs = {
            "legacy-copilot": {
                "implementer": {"backend": "codex", "model": cfg["openai_model"]},
                "reviewer": {
                    "backend": "copilot",
                    "model": copilot_model or review_policy.PROVIDERS["copilot"]["model"],
                    "effort": (cfg.get("review_effort") if provider == "copilot" else None) or "default",
                },
            }
        }
        default = "legacy-copilot"
        if provider == "claude-code":
            reviewer = {
                "backend": "claude-code",
                "model": cfg.get("review_model") or review_policy.PROVIDERS["claude-code"]["model"],
                "effort": cfg.get("review_effort") or review_policy.PROVIDERS["claude-code"]["effort"],
            }
            if cfg.get("review_max_estimated_usd") is not None:
                reviewer["max_estimated_usd"] = cfg["review_max_estimated_usd"]
            specs["legacy-claude-code"] = {
                "implementer": {"backend": "codex", "model": cfg["openai_model"]},
                "reviewer": reviewer,
            }
            default = "legacy-claude-code"
        return specs, default, "legacy-copilot", True, True
    if version == 3:
        if has_models or has_review:
            raise WorkflowError(
                "Schema 3 declares the reviewer inside profiles; remove openai_model, copilot_model and review_*"
            )
        return cfg.get("profiles"), cfg.get("default_profile"), cfg.get("hosted_profile"), False, True
    raise WorkflowError("Unsupported .agentic/config.json schema (expected 1, 2 or 3)")


def load_profiles(cfg):
    specs, default, hosted, shimmed, allow_claude_code = shimmed_specs(cfg)
    if not isinstance(specs, dict) or not specs:
        raise WorkflowError("Configuration needs a nonempty profiles object")
    if default not in specs:
        raise WorkflowError("default_profile must name a declared profile")
    profiles = {}
    for name, spec in specs.items():
        if not isinstance(name, str) or not re.fullmatch(NAME_PATTERN, name):
            raise WorkflowError(f"Profile name {name!r} must be 1-40 lowercase letters, digits or hyphens")
        if not isinstance(spec, dict) or set(spec) != {"implementer", "reviewer"}:
            raise WorkflowError(f"Profile {name!r} must declare exactly implementer and reviewer")
        implementer = normalize_implementer(name, spec["implementer"])
        reviewer = normalize_reviewer(name, spec["reviewer"], cfg, allow_claude_code=allow_claude_code)
        profiles[name] = {
            "name": name,
            "implementer": implementer,
            "reviewer": reviewer,
            "same_family": implementer["family"] == reviewer["family"],
        }
    if hosted is not None:
        if hosted not in profiles:
            raise WorkflowError("hosted_profile must name a declared profile")
        if profiles[hosted]["reviewer"]["backend"] != "copilot":
            raise WorkflowError(
                "hosted_profile must resolve to a copilot reviewer; hosted review is Copilot-only"
            )
    return {
        "default_profile": default,
        "hosted_profile": hosted,
        "profiles": profiles,
        "config_schema": cfg.get("schema_version"),
        "shimmed": shimmed,
    }


def migration_hint(declared):
    return {
        "schema_version": 3,
        "default_profile": declared["default_profile"],
        **({"hosted_profile": declared["hosted_profile"]} if declared["hosted_profile"] else {}),
        "profiles": {
            name: {
                "implementer": {key: value for key, value in p["implementer"].items() if key != "family"},
                "reviewer": {
                    key: value
                    for key, value in p["reviewer"].items()
                    if key != "family" and value is not None
                },
            }
            for name, p in declared["profiles"].items()
        },
    }


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
    if declared["shimmed"]:
        hint = json.dumps(migration_hint(declared), separators=(",", ":"), sort_keys=True)
        # One note per shimmed configuration per process; helpers resolve the profile repeatedly.
        if (declared["config_schema"], hint) not in _NOTED_MIGRATIONS:
            _NOTED_MIGRATIONS.add((declared["config_schema"], hint))
            note(
                "configuration schema "
                f"{declared['config_schema']} was shimmed into profiles; the schema 3 equivalent is " + hint
            )
    stale = plain_path(private_root(repo) / "review-selection.json")
    if stale.exists():
        warn(
            "saved review selection (.agentic-local/review-selection.json) is no longer honored; use `profile use`"
        )
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


LOGIN_ROOT_FILE = ".agentic-local/claude-login-root.json"


def login_root_path(repo):
    return plain_path(private_root(repo) / "claude-login-root.json")


def local_login_root(repo):
    """The per-machine native login-store override: ignored, untracked, operator-written."""
    path = login_root_path(repo)
    if not path.exists():
        return None
    message = f"Local login-root override ({LOGIN_ROOT_FILE}) is malformed; run `workflow.py claude-login-root clear`"
    if not path.is_file():
        raise WorkflowError("Local login-root override is not a regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise WorkflowError(message) from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or not isinstance(value.get("login_root"), str)
        or not isinstance(value.get("reason"), str)
    ):
        raise WorkflowError(message)
    return {**value, "login_root": absolute_path(value["login_root"], "Local login-root override")}


def login_root_resolution(cfg, reviewer, provider, repo=None):
    """Where the native login store comes from: local override, profile, configuration, default.

    ``login_root`` stays ``None`` for the built-in default so that ``claude_native_auth``
    resolves it; ``effective_login_root`` names the directory that will actually be used.
    """
    if provider != "claude-code":
        return {"login_root": None, "login_root_source": None}
    local = local_login_root(repo) if repo is not None else None
    if local:
        return {"login_root": local["login_root"], "login_root_source": "local-file"}
    if reviewer["backend"] == "claude-code" and reviewer.get("login_root"):
        return {"login_root": reviewer["login_root"], "login_root_source": "profile"}
    configured = absolute_path(cfg.get("claude_review_login_root"), "claude_review_login_root")
    if configured:
        return {"login_root": configured, "login_root_source": "configuration"}
    return {"login_root": None, "login_root_source": "default"}


def login_root(cfg, reviewer, provider, repo=None):
    """The native login store for the selected provider (``None`` means the built-in default)."""
    return login_root_resolution(cfg, reviewer, provider, repo)["login_root"]


def effective_login_root(value):
    import claude_native_auth

    return str(Path(value).expanduser() if value else claude_native_auth.default_root())


def setup_login_root(repo, cfg=None):
    """The store a human login or renewal registers into, independent of the active provider."""
    cfg = configuration(repo.root) if cfg is None else cfg
    profile = active_profile(repo, cfg, warn_same_family=False)
    resolution = login_root_resolution(cfg, profile["reviewer"], "claude-code", repo)
    return {**resolution, "effective_login_root": effective_login_root(resolution["login_root"])}


def use_login_root(repo, path, reason):
    if os.environ.get("AGENTIC_EXECUTOR_ROLE"):
        raise WorkflowError("Managed executors must not change the native login root")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise WorkflowError("A recorded reason of 1-2000 characters is required")
    if not isinstance(path, str) or not path.strip():
        raise WorkflowError("Login root must be an absolute path")
    record = {
        "schema_version": 1,
        "login_root": absolute_path(path, "Login root"),
        "reason": reason,
        "recorded_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    atomic_json(login_root_path(repo), record)
    return record


def clear_login_root(repo):
    if os.environ.get("AGENTIC_EXECUTOR_ROLE"):
        raise WorkflowError("Managed executors must not change the native login root")
    path = login_root_path(repo)
    existed = path.exists()
    if existed:
        path.unlink()
    return {"cleared": existed, **setup_login_root(repo)}


def show_login_root(repo):
    local = local_login_root(repo)
    return {
        "local_file": str(login_root_path(repo)) if local else None,
        "local_override": local,
        **setup_login_root(repo),
    }


def effective_budget_config(cfg, reviewer, provider):
    """Top-level budgets, replaced by the profile's own values only for its own backend."""
    budgets = {
        "review_timeout_seconds": cfg.get("review_timeout_seconds", 900),
        "review_max_ai_credits": cfg.get("review_max_ai_credits", 400),
        "review_max_estimated_usd": cfg.get("review_max_estimated_usd", 10),
        "review_model_extensions": cfg.get("review_model_extensions", []),
        "schema_version": cfg.get("schema_version"),
    }
    if provider == reviewer["backend"]:
        if reviewer.get("timeout_seconds") is not None:
            budgets["review_timeout_seconds"] = reviewer["timeout_seconds"]
        if provider == "copilot" and reviewer.get("max_ai_credits") is not None:
            budgets["review_max_ai_credits"] = reviewer["max_ai_credits"]
        if provider == "claude-code" and reviewer.get("max_estimated_usd") is not None:
            budgets["review_max_estimated_usd"] = reviewer["max_estimated_usd"]
    return budgets


def review_selection(
    repo,
    cfg=None,
    *,
    review_provider=None,
    review_model=None,
    review_effort=None,
    implementer=None,
    allow_same_family=False,
    require_backend=None,
    warn_same_family=True,
    review_exception=None,
):
    """Resolve the active profile plus explicit per-call overrides into an immutable review policy."""
    cfg = configuration(repo.root) if cfg is None else cfg
    profile = active_profile(repo, cfg, warn_same_family=False)
    reviewer = profile["reviewer"]
    selected = {"provider": reviewer["backend"], "model": reviewer["model"], "effort": reviewer["effort"]}
    sources = dict.fromkeys(selected, "profile")
    overrides = None
    if any(value is not None for value in (review_provider, review_model, review_effort)):
        overrides = {
            "review_provider": review_provider,
            "review_model": review_model,
            "review_effort": review_effort,
        }
        if review_provider is not None:
            selected = review_policy.choices(review_provider)
            sources = dict.fromkeys(selected, "per-call-provider-default")
            sources["provider"] = "per-call"
        for key, value in (("model", review_model), ("effort", review_effort)):
            if value is not None:
                selected[key], sources[key] = value, "per-call"
    if require_backend is not None and selected["provider"] != require_backend:
        raise WorkflowError(
            f"This path requires a reviewer backend of {require_backend}; profile {profile['name']!r} "
            f"resolves to {selected['provider']}"
        )
    policy = review_policy.policy(
        selected, effective_budget_config(cfg, reviewer, selected["provider"]), exception=review_exception
    )
    reviewer_family = family(policy["model"])
    if implementer is None:
        declared = profile["implementer"]
        implementer = {
            "backend": declared["backend"],
            "model": declared["model"],
            "family": declared["family"],
        }
        implementer_source = "active profile (no task record)"
    else:
        implementer_source = "pinned executor"
    same_family = reviewer_family == implementer["family"]
    declared = profile["implementer"]
    # A recorded profile-level allowance covers only the pairing it was recorded for:
    # the profile's own declared implementer and reviewer. Overrides never inherit it.
    recorded_applies = (
        profile["allow_same_family"]
        and overrides is None
        and implementer["backend"] == declared["backend"]
        and implementer.get("model") in (None, declared["model"])
    )
    acknowledged = bool(allow_same_family or recorded_applies)
    if same_family and not acknowledged:
        raise WorkflowError(
            f"Reviewer model {policy['model']} shares the implementer family ({reviewer_family}); "
            "switch profile or pass --allow-same-family to record this exception"
            + ("; a recorded profile allowance never covers a per-call override" if overrides else "")
        )
    if same_family and warn_same_family:
        warn(f"review selection pairs same-family implementer and reviewer ({reviewer_family})")
    return {
        "profile": profile["name"],
        "policy": policy,
        "sources": sources,
        "overrides": overrides,
        "provenance": {
            "profile": profile["name"],
            "implementer": implementer,
            "implementer_source": implementer_source,
            "same_family": same_family,
            "same_family_acknowledged": acknowledged if same_family else None,
        },
        **login_root_resolution(cfg, reviewer, selected["provider"], repo),
        "reviewer_family": reviewer_family,
    }


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
    cfg = configuration(repo.root) if cfg is None else cfg
    profile = active_profile(repo, cfg)
    local = local_selection(repo)
    declared = load_profiles(cfg)
    result = {
        **profile,
        "local_file": str(local_path(repo)) if local else None,
        "local_selection": local,
        "config_schema": declared["config_schema"],
        "shimmed": declared["shimmed"],
        "hosted_profile": declared["hosted_profile"],
        "tools": {tool: shutil.which(tool) for tool in ("codex", "claude", "copilot")},
    }
    try:
        selection = review_selection(repo, cfg, warn_same_family=False)
        result["review_policy"] = review_policy.status(repo, cfg, selection)
        result["login_root_source"] = selection["login_root_source"]
        result["login_root"] = (
            effective_login_root(selection["login_root"]) if selection["login_root_source"] else None
        )
    except WorkflowError as exc:
        result["review_policy"] = {"error": str(exc)}
    return result


def listing(repo, cfg=None):
    cfg = configuration(repo.root) if cfg is None else cfg
    declared = load_profiles(cfg)
    try:
        active, error = active_profile(repo, cfg, warn_same_family=False)["name"], None
    except WorkflowError as exc:
        active, error = None, str(exc)
    return {
        "default_profile": declared["default_profile"],
        "hosted_profile": declared["hosted_profile"],
        "config_schema": declared["config_schema"],
        "shimmed": declared["shimmed"],
        "active": active,
        "active_error": error,
        "profiles": {
            name: {
                "implementer": {"backend": p["implementer"]["backend"], "model": p["implementer"]["model"]},
                "reviewer": {
                    "backend": p["reviewer"]["backend"],
                    "model": p["reviewer"]["model"],
                    "effort": p["reviewer"]["effort"],
                },
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
    login = sub.add_parser(
        "claude-login-root",
        help="Show, record or clear the per-machine native Claude login store override",
    )
    login_actions = login.add_subparsers(dest="login_root_command", required=True)
    login_actions.add_parser("show")
    use_root = login_actions.add_parser("use")
    use_root.add_argument("path")
    use_root.add_argument("--reason", required=True)
    login_actions.add_parser("clear")
    setup = sub.add_parser(
        "claude-login-setup",
        help="Register or renew the dedicated Max login for the native Claude reviewer (human, real terminal)",
    )
    setup.add_argument("--renew", action="store_true")
    setup.add_argument("--paid-usage-disabled", action="store_true")


def dispatch_login_root(repo, args):
    if args.login_root_command == "show":
        return show_login_root(repo)
    if args.login_root_command == "use":
        return use_login_root(repo, args.path, args.reason)
    if args.login_root_command == "clear":
        return clear_login_root(repo)
    raise WorkflowError("Unknown login-root operation")


def login_setup(repo, args):
    """Human-only registration into the resolved store; nothing printed identifies the account."""
    import claude_native_auth

    resolution = setup_login_root(repo)
    result = claude_native_auth.setup(
        repo, root=resolution["login_root"], renew=args.renew, paid_usage_disabled=args.paid_usage_disabled
    )
    return {
        **result,
        "login_root": resolution["effective_login_root"],
        "login_root_source": resolution["login_root_source"],
        "renewed": bool(args.renew),
    }


def dispatch(repo, args):
    if args.command == "claude-login-root":
        return dispatch_login_root(repo, args)
    if args.command == "claude-login-setup":
        return login_setup(repo, args)
    if args.profile_command == "show":
        return show(repo)
    if args.profile_command == "list":
        return listing(repo)
    if args.profile_command == "use":
        return use_profile(repo, args.name, args.allow_same_family, args.reason)
    if args.profile_command == "clear":
        return clear_profile(repo)
    raise WorkflowError("Unknown profile operation")
