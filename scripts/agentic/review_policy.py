"""Immutable, provider-specific review execution policy built from the active profile."""

from __future__ import annotations

import copy
import re
from urllib.parse import urlsplit

from copilot_policy import CLI_ARCHIVE_SHA256, CLI_VERSION
from workflow import WorkflowError

# Exact provider spellings, not aliases. This compatibility table is deliberately
# bounded to documented models/efforts; account entitlement is a separate
# preflight/runtime concern and never authorizes fallback to another entry.
CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"]
MODELS = {
    "claude-code": {
        "claude-opus-5-5": CLAUDE_EFFORTS,
        "claude-opus-5": CLAUDE_EFFORTS,
        "claude-sonnet-5": CLAUDE_EFFORTS,
        "claude-opus-4-7": CLAUDE_EFFORTS,
        "claude-sonnet-4-6": ["low", "medium", "high", "max"],
    },
    "copilot": {
        "claude-opus-5": ["default", *CLAUDE_EFFORTS],
        "claude-opus-5.5": ["default", *CLAUDE_EFFORTS],
        "claude-sonnet-5": ["default", *CLAUDE_EFFORTS],
        "claude-sonnet-4.6": ["default", "low", "medium", "high", "max"],
        "claude-haiku-4.5": ["default"],
        "gpt-5.4": ["default", "none", "low", "medium", "high", "xhigh"],
        "gpt-6-astra": ["default"],
        "gpt-6-sol": ["default"],
        "gpt-6-luna": ["default"],
    },
}

PROVIDERS = {
    "copilot": {
        "model": "claude-opus-5",
        "effort": "default",
        "efforts": ["default"],
        "cli": {"version": CLI_VERSION, "platform": "linux-x64", "archive_sha256": CLI_ARCHIVE_SHA256},
        "adapter": "copilot-session-events-v2",
        "billing_mode": "copilot-ai-credits",
    },
    "claude-code": {
        "model": "claude-opus-5-5",
        "effort": "medium",
        "efforts": ["low", "medium", "high", "xhigh", "max"],
        "cli": {
            "version": "2.1.282",
            "platform": "linux-x64",
            "binary_sha256": "3afe8535c0cc33f0e24f7b25dab7a1727b8b592196f8496a8bc302ba2161eed3",
            "manifest_sha256": "041abb14aba47e7dd31f8ba83d8102e54b6350d099382cb1def7ab10add415ed",
            "signing_fingerprint": "31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE",
        },
        "adapter": "claude-stream-json-2.1.282-v6",
        "billing_mode": "included-max-subscription-only",
    },
}
EXTENSION_SCHEMAS = {2, 3}
# A per-call, reason-bearing budget exception may raise a Claude review above the policy
# defaults (900 s, $10 reference) up to these ceilings; never a configured default.
EXCEPTION_MAX_TIMEOUT_SECONDS = 7200
EXCEPTION_MAX_ESTIMATED_USD = 60


def model_extensions(cfg):
    """Validate trusted compatibility declarations, never discover/fetch models.

    These declarations require operator verification against primary evidence.
    URL syntax checks cannot establish that evidence's truth or account entitlement.
    Records travel with packets so later configuration cannot reinterpret recovery.
    """
    entries = cfg.get("review_model_extensions", [])
    if not isinstance(entries, list) or len(entries) > 64:
        raise WorkflowError("Invalid review model compatibility declarations")
    if entries and cfg.get("schema_version") not in EXTENSION_SCHEMAS:
        raise WorkflowError("Model compatibility extensions require configuration schema 2 or 3")
    result = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "provider",
            "model",
            "efforts",
            "cli_version",
            "adapter",
            "evidence",
        }:
            raise WorkflowError("Incomplete model compatibility declaration")
        provider, model = entry["provider"], entry["model"]
        if not isinstance(provider, str) or provider not in PROVIDERS:
            raise WorkflowError("Unsupported model compatibility provider")
        spec = PROVIDERS[provider]
        if (
            not isinstance(model, str)
            or len(model) > 128
            or not re.fullmatch(r"[a-z][a-z0-9]*(?:[-.][a-z0-9]+)+", model)
            or not re.search(r"\d", model)
            or {"auto", "default", "latest"}.intersection(re.split(r"[-.]", model))
            or (provider == "claude-code" and not model.startswith("claude-"))
            or model in MODELS[provider]
            or (provider, model) in result
        ):
            raise WorkflowError("Model extension must name a unique exact model, not an alias or override")
        efforts = entry["efforts"]
        allowed = (
            CLAUDE_EFFORTS
            if provider == "claude-code"
            else ["default", "none", "minimal", "low", "medium", "high", "xhigh", "max"]
        )
        if (
            not isinstance(efforts, list)
            or not efforts
            or any(not isinstance(item, str) or item not in allowed for item in efforts)
            or len(set(efforts)) != len(efforts)
            or entry["cli_version"] != spec["cli"]["version"]
            or entry["adapter"] != spec["adapter"]
        ):
            raise WorkflowError("Model extension is incompatible with pinned CLI/adapter/effort controls")
        evidence = entry["evidence"]
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 8:
            raise WorkflowError("Model compatibility requires primary-source evidence references")
        for reference in evidence:
            if not isinstance(reference, str) or len(reference) > 512 or not reference.isascii():
                raise WorkflowError("Invalid model compatibility evidence reference")
            try:
                url = urlsplit(reference)
            except ValueError:
                raise WorkflowError("Invalid model compatibility evidence reference") from None
            domains = (
                {"code.claude.com", "platform.claude.com"}
                if provider == "claude-code"
                else {"docs.github.com"}
            )
            if (
                url.scheme != "https"
                or url.netloc not in domains
                or not url.path.startswith("/")
                or url.query
                or any(char.isspace() or ord(char) < 32 for char in reference)
            ):
                raise WorkflowError(
                    "Model compatibility evidence must reference public primary documentation"
                )
        result[provider, model] = copy.deepcopy(entry)
    return result


def model_catalog(cfg):
    catalog = copy.deepcopy(MODELS)
    for (provider, model), entry in model_extensions(cfg).items():
        catalog[provider][model] = entry["efforts"]
    return catalog


def choices(provider, model=None, effort=None, *, cfg=None):
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise WorkflowError(f"Unsupported review provider {provider!r}; expected copilot or claude-code")
    spec = PROVIDERS[provider]
    model = spec["model"] if model is None else model
    effort = spec["effort"] if effort is None else effort
    catalog = model_catalog(cfg or {})[provider]
    if not isinstance(model, str) or model not in catalog:
        raise WorkflowError(
            f"Unsupported exact review model {model!r} for provider {provider}; known models: "
            + ", ".join(sorted(catalog))
        )
    if effort not in catalog[model]:
        raise WorkflowError(
            f"Unsupported effort {effort!r} for {provider} model {model}; allowed: "
            + ", ".join(catalog[model])
        )
    return {"provider": provider, "model": model, "effort": effort}


def budget(provider, cfg, *, diagnostic=False):
    if diagnostic:
        if provider != "claude-code":
            raise WorkflowError("Activation diagnostics are Claude-only")
        return {
            "schema_version": 1,
            "kind": "reference-usd",
            "timeout_seconds": 300,
            "estimated_usd": 2,
            "extra_spend_authorized_usd": 0,
        }
    timeout = cfg.get("review_timeout_seconds", 900)
    if type(timeout) is not int or not 0 < timeout <= 900:
        raise WorkflowError("Reviewer timeout must be at most 900 seconds")
    if provider == "copilot":
        amount = cfg.get("review_max_ai_credits", 400)
        if type(amount) is not int or amount <= 0:
            raise WorkflowError("Invalid Copilot credit budget")
        return {"schema_version": 1, "kind": "ai-credits", "timeout_seconds": timeout, "ai_credits": amount}
    amount = cfg.get("review_max_estimated_usd", 10)
    if type(amount) not in {int, float} or not 0 < amount <= 10:
        raise WorkflowError("Claude reference-cost ceiling must be positive and at most $10")
    return {
        "schema_version": 1,
        "kind": "reference-usd",
        "timeout_seconds": timeout,
        "estimated_usd": amount,
        "extra_spend_authorized_usd": 0,
    }


def policy(selection, cfg, *, diagnostic=False):
    selected = choices(**selection, cfg=cfg)
    spec = PROVIDERS[selected["provider"]]
    value = {
        "schema_version": 1,
        **selected,
        "cli": copy.deepcopy(spec["cli"]),
        "adapter": spec["adapter"],
        "billing_mode": spec["billing_mode"],
        "budget": budget(selected["provider"], cfg, diagnostic=diagnostic),
    }
    extension = model_extensions(cfg).get((selected["provider"], selected["model"]))
    if extension is not None:
        value["model_compatibility"] = extension
    return value


def validate_policy(value):
    if not isinstance(value, dict):
        raise WorkflowError("Missing immutable review policy")
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise WorkflowError("Unsupported review policy version")
    selected = {key: value.get(key) for key in ("provider", "model", "effort")}
    cfg = {}
    if "model_compatibility" in value:
        cfg["review_model_extensions"] = [copy.deepcopy(value["model_compatibility"])]
        cfg["schema_version"] = 3
    selected = choices(**selected, cfg=cfg)
    limits = value.get("budget")
    if not isinstance(limits, dict):
        raise WorkflowError("Missing provider-specific budget")
    if type(limits.get("schema_version")) is not int or limits["schema_version"] != 1:
        raise WorkflowError("Unsupported review budget version")
    if selected["provider"] == "claude-code" and (
        type(limits.get("extra_spend_authorized_usd")) is not int or limits["extra_spend_authorized_usd"] != 0
    ):
        raise WorkflowError("Claude extra spending is not authorized")
    cfg.update(
        {
            "review_timeout_seconds": limits.get("timeout_seconds"),
            "review_max_ai_credits": limits.get("ai_credits"),
            "review_max_estimated_usd": limits.get("estimated_usd"),
        }
    )
    expected = policy(selected, cfg)
    if "authentication" in value:
        if selected["provider"] != "claude-code":
            raise WorkflowError("Native authentication cannot bind a different provider")
        try:
            import claude_native_auth
        except ImportError:
            raise WorkflowError(
                "This policy binds a native Claude login, but the Claude Code reviewer adapter is not installed"
            ) from None
        expected["authentication"] = claude_native_auth.validate_binding(
            value["authentication"], current=False
        )
    if value != expected:
        raise WorkflowError("Immutable review policy differs from supported provider bindings")
    return value


def add_arguments(parser):
    for field in ("provider", "model", "effort"):
        parser.add_argument("--review-" + field)


def status(repo, cfg, selection):
    """Report what the selected policy still needs before a review could run."""
    result = {
        "profile": selection["profile"],
        "policy": selection["policy"],
        "sources": selection["sources"],
        "overrides": selection["overrides"],
        "provenance": selection["provenance"],
    }
    provider = selection["policy"]["provider"]
    blockers = []
    import review_cli

    try:
        review_cli.executable(repo, provider)
    except (WorkflowError, OSError, ValueError):
        blockers.append("verified_pinned_cli_unavailable")
    if provider == "claude-code":
        try:
            import claude_activation
            import claude_native_auth
            import review_claude
        except ImportError:
            blockers.append("claude_reviewer_adapter_not_installed")
        else:
            native = claude_native_auth.status(
                selection["policy"]["budget"]["timeout_seconds"], root=selection["login_root"]
            )
            result["native_authentication"] = native
            blockers.extend(native["blockers"])
            try:
                review_claude.managed_controls()
            except (WorkflowError, OSError):
                blockers.append("managed_controls_require_verification")
            try:
                bound = {**selection["policy"]}
                if "authentication" in native:
                    bound["authentication"] = native["authentication"]
                result["native_capability"] = claude_activation.require(
                    repo, bound, login_root=selection["login_root"]
                )
            except (WorkflowError, OSError, ValueError, KeyError):
                blockers.append("matching_native_capability_diagnostic_unavailable")
    result["activation_blockers"] = blockers
    result["supported_models"] = model_catalog(cfg)[provider]
    result["model_compatibility_sources"] = {
        model: "built-in" if model in MODELS[provider] else "trusted-config-declaration"
        for model in result["supported_models"]
    }
    result["note"] = (
        "Selection is not activation or evidence of included billing, isolation, capability or review readiness."
    )
    return result


def require_current_adapter(value):
    validate_policy(value)
    if value["adapter"] != PROVIDERS[value["provider"]]["adapter"]:
        raise WorkflowError("Historical review adapter is recovery-only; prepare a fresh packet")
