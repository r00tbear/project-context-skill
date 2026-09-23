"""Small stdlib validator for project-context generated files."""

import argparse
import difflib
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import webbrowser
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any, cast
from urllib.parse import urlsplit

CONFIG_PATH = "repodocs/project-context.config.json"
MANIFEST_PATH = "repodocs/project-context.manifest.json"
PROJECT_MAP_PATH = "repodocs/project-map.json"
AUDITORS = {
    "stack",
    "architecture",
    "ui",
    "data",
    "bloat",
    "performance",
    "security",
    "testing",
    "greenfield",
}
HOST_FILES = {"claude": "CLAUDE.md", "codex": "AGENTS.md"}
HOST_MARKERS = {
    host: (
        f"<!-- project-context:{host}:begin v1 -->",
        f"<!-- project-context:{host}:end v1 -->",
    )
    for host in HOST_FILES
}
HASH_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
LANGUAGE_RE = re.compile(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*\Z")
ID_RE = re.compile(r"[a-z][a-z0-9_-]*\Z")
FINDING_ID_RE = re.compile(r"[a-z]+-[0-9]{3,}\Z")
WIKILINK_RE = re.compile(
    r"\[\[([a-z][a-z0-9_-]*)(?:#([A-Za-z0-9][A-Za-z0-9_-]*))?(?:\|[^\]\n]+)?\]\]"
)
WIKILINK_TOKEN_RE = re.compile(r"\[\[([^\[\]\n]+)\]\]")
FENCED_CODE_RE = re.compile(
    r"^ {0,3}(```|~~~).*?^ {0,3}\1[ \t]*$", re.MULTILINE | re.DOTALL
)
INLINE_CODE_RE = re.compile(r"``[^`\n](?:[^`\n]|`(?!`))*``|`[^`\n]*`")


def strip_code_spans(text: str) -> str:
    """Code spans may quote a wikilink without creating one; drop them before link extraction."""
    return INLINE_CODE_RE.sub("", FENCED_CODE_RE.sub("", text))


CORE_DOMAINS = {"architecture", "stack", "security", "testing"}
ALL_DOMAINS = CORE_DOMAINS | {"ui", "data"}
ARTIFACT_KINDS = {"owned_file", "managed_block"}
SEVERITIES = {"low", "medium", "high", "critical"}
SEVERITY_RANK = {
    name: rank for rank, name in enumerate(("low", "medium", "high", "critical"))
}
BASE_AUDITORS = {"stack", "architecture", "bloat", "security", "testing"}
PROJECT_MAP_KINDS = {"surface", "component", "data", "runtime", "external", "other"}
PROJECT_MAP_STATUSES = {"current", "planned", "legacy"}


class ContractError(ValueError):
    """A user-correctable contract violation."""

    def __init__(self, message: str, code: int = 4) -> None:
        super().__init__(message)
        self.code = code


def _reject_constant(value: str) -> None:
    raise ContractError(f"non-finite JSON number is not allowed: {value}")


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def strict_json_loads(text: str, label: str = "JSON") -> Any:
    """Parse JSON while rejecting duplicate keys and NaN/Infinity."""
    try:
        return json.loads(
            text,
            object_pairs_hook=_object,
            parse_constant=_reject_constant,
        )
    except ContractError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid {label}: {exc}") from exc


def load_json(path: Path) -> Any:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ContractError(f"cannot read {path}: {exc.strerror or exc}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError(f"{path} is not UTF-8") from exc
    return strict_json_loads(text, str(path))


def dump(value: Any) -> None:
    print(
        json.dumps(value, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True)
    )


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{label} must be an array")
    return value


def _text(value: Any, label: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        raise ContractError(
            f"{label} must be a{' non-empty' if nonempty else ''} string"
        )
    return value


def _exact_keys(
    value: dict[str, Any],
    required: set[str],
    label: str,
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    missing = required - value.keys()
    extra = value.keys() - required - optional
    if missing:
        raise ContractError(f"{label} is missing: {', '.join(sorted(missing))}")
    if extra:
        raise ContractError(f"{label} has unknown keys: {', '.join(sorted(extra))}")


def _enum(value: Any, choices: set[str], label: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ContractError(f"{label} must be one of: {', '.join(sorted(choices))}")
    return value


def _unique_strings(
    value: Any, label: str, choices: set[str] | None = None
) -> list[str]:
    items = _list(value, label)
    if not all(isinstance(item, str) for item in items):
        raise ContractError(f"{label} must contain only strings")
    if len(items) != len(set(items)):
        raise ContractError(f"{label} must not contain duplicates")
    if choices is not None:
        unknown = set(items) - choices
        if unknown:
            raise ContractError(
                f"{label} contains unknown values: {', '.join(sorted(unknown))}"
            )
    return items


def _timestamp(value: Any, label: str) -> str:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ContractError(f"{label} must include a timezone")
    return text


def validate_relative_path(value: Any, label: str = "path") -> str:
    text = _text(value, label)
    if len(text) > 4096 or "\\" in text or any(ord(char) < 32 for char in text):
        raise ContractError(f"{label} is not a safe repository-relative path")
    path = PurePosixPath(text)
    if path.is_absolute() or text.endswith("/") or "//" in text:
        raise ContractError(f"{label} is not a safe repository-relative path")
    if text == "." or any(part in {"", ".", ".."} for part in text.split("/")):
        raise ContractError(f"{label} is not a safe repository-relative path")
    if any(part.casefold() == ".git" for part in path.parts):
        raise ContractError(f"{label} must not enter .git")
    return path.as_posix()


def safe_path(root: Path, relative: Any, *, must_exist: bool = False) -> Path:
    """Resolve a repository path without following any in-tree symlink."""
    rel = validate_relative_path(relative)
    root = root.resolve(strict=True)
    candidate = root
    for part in PurePosixPath(rel).parts:
        candidate = candidate / part
        if os.path.lexists(candidate):
            try:
                lstat_result = os.lstat(candidate)
            except OSError as exc:
                raise ContractError(
                    f"cannot inspect {rel}: {exc.strerror or exc}"
                ) from exc
            # Junctions (IO_REPARSE_TAG_MOUNT_POINT) are the reparse points that behave like
            # directory symlinks; other reparse tags (cloud placeholders, app-exec aliases)
            # are ordinary files and must not be rejected.
            reparse_tag = getattr(lstat_result, "st_reparse_tag", 0)
            if stat.S_ISLNK(lstat_result.st_mode) or reparse_tag == getattr(
                stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003
            ):
                raise ContractError(f"symlink is not allowed in generated path: {rel}")
    try:
        candidate.resolve(strict=False).relative_to(root)
    except (OSError, ValueError) as exc:
        raise ContractError(f"path escapes repository root: {rel}") from exc
    if must_exist and not os.path.lexists(candidate):
        raise ContractError(f"required artifact is missing: {rel}")
    return candidate


def _lexists_in_safe_parent(root: Path, relative: str) -> bool:
    parent = PurePosixPath(relative).parent
    if parent != PurePosixPath("."):
        safe_path(root, parent.as_posix())
    return os.path.lexists(root / relative)


def _read_regular(path: Path, label: str) -> bytes:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise ContractError(f"cannot inspect {label}: {exc.strerror or exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ContractError(f"{label} must be a regular file")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ContractError(f"cannot read {label}: {exc.strerror or exc}") from exc


def validate_config(value: Any) -> dict[str, Any]:
    config = _mapping(value, "config")
    _exact_keys(
        config,
        {
            "schema_version",
            "user_level",
            "language",
            "document_layout",
            "domains",
            "hosts",
            "audit",
        },
        "config",
    )
    if type(config["schema_version"]) is not int or config["schema_version"] != 1:
        raise ContractError("config.schema_version must be 1")
    _enum(config["user_level"], {"novice", "specialist", "expert"}, "config.user_level")
    if not isinstance(config["language"], str) or not LANGUAGE_RE.fullmatch(
        config["language"]
    ):
        raise ContractError("config.language must be a BCP-47-style language tag")
    _enum(config["document_layout"], {"compact", "full"}, "config.document_layout")
    domains = _mapping(config["domains"], "config.domains")
    _exact_keys(domains, {"ui", "data"}, "config.domains")
    for name, state in domains.items():
        _enum(state, {"auto", "enabled", "disabled"}, f"config.domains.{name}")
    hosts = _mapping(config["hosts"], "config.hosts")
    _exact_keys(hosts, set(HOST_FILES), "config.hosts")
    if not all(type(enabled) is bool for enabled in hosts.values()):
        raise ContractError("config.hosts values must be booleans")
    audit = _mapping(config["audit"], "config.audit")
    _exact_keys(audit, {"exclude"}, "config.audit")
    excludes = _unique_strings(audit["exclude"], "config.audit.exclude")
    for index, path in enumerate(excludes):
        if any(character in path for character in "*?[") or path.startswith(":"):
            raise ContractError(
                f"config.audit.exclude[{index}] contains glob or pathspec-magic characters; "
                f"exclusions are literal repository-relative paths: {path}"
            )
        validate_relative_path(path, f"config.audit.exclude[{index}]")
    return config


def _validate_scope(value: Any, label: str) -> dict[str, Any]:
    scope = _mapping(value, label)
    _exact_keys(scope, {"included", "excluded", "unscanned"}, label, {"limitations"})
    for key in ("included", "excluded", "unscanned"):
        paths = _unique_strings(scope[key], f"{label}.{key}")
        for index, path in enumerate(paths):
            if any(character in path for character in "*?["):
                raise ContractError(
                    f"{label}.{key}[{index}] contains glob characters; scope entries are literal repository-relative paths: {path}"
                )
            if path != ".":
                validate_relative_path(path, f"{label}.{key}[{index}]")
    if "limitations" in scope:
        limitations = _list(scope["limitations"], f"{label}.limitations")
        for index, item in enumerate(limitations):
            _text(item, f"{label}.limitations[{index}]")
    return scope


def _covered_by(path: str, roots: list[str]) -> bool:
    return any(
        root == "." or path == root or path.startswith(root + "/") for root in roots
    )


def _validate_evidence(value: Any, label: str) -> dict[str, Any]:
    evidence = _mapping(value, label)
    _exact_keys(evidence, {"path", "detail"}, label, {"line"})
    validate_relative_path(evidence["path"], f"{label}.path")
    _text(evidence["detail"], f"{label}.detail")
    if "line" in evidence and (
        type(evidence["line"]) is not int or evidence["line"] < 1
    ):
        raise ContractError(f"{label}.line must be a positive integer")
    return evidence


def validate_findings(
    value: Any, previous: Any | None = None, *, allow_provisional: bool = False
) -> dict[str, Any]:
    document = _mapping(value, "findings document")
    _exact_keys(
        document,
        {"schema_version", "run_id", "auditor", "scanned_at", "scope", "findings"},
        "findings document",
    )
    if type(document["schema_version"]) is not int or document[
        "schema_version"
    ] not in {2, 3}:
        raise ContractError("findings.schema_version must be 2 or 3")
    _text(document["run_id"], "findings.run_id")
    _enum(document["auditor"], AUDITORS, "findings.auditor")
    _timestamp(document["scanned_at"], "findings.scanned_at")
    _validate_scope(document["scope"], "findings.scope")
    records = _list(document["findings"], "findings.findings")
    ids: set[str] = set()
    for index, raw in enumerate(records):
        label = f"findings.findings[{index}]"
        finding = _mapping(raw, label)
        _exact_keys(
            finding,
            {
                "id",
                "kind",
                "title",
                "severity",
                "confidence",
                "identity",
                "status",
                "evidence",
                "verification",
            },
            label,
            {"remediation"} if document["schema_version"] == 3 else set(),
        )
        finding_id = _text(finding["id"], f"{label}.id")
        if (
            not FINDING_ID_RE.fullmatch(finding_id)
            or not finding_id.startswith(document["auditor"] + "-")
            or finding_id in ids
        ):
            raise ContractError(
                f"{label}.id must be unique and prefixed by its auditor"
            )
        ids.add(finding_id)
        kind = _text(finding["kind"], f"{label}.kind")
        if not re.fullmatch(r"[a-z][a-z0-9-]*", kind):
            raise ContractError(f"{label}.kind is invalid")
        _text(finding["title"], f"{label}.title")
        severity = _enum(finding["severity"], SEVERITIES, f"{label}.severity")
        _enum(finding["confidence"], {"low", "medium", "high"}, f"{label}.confidence")
        identity = _mapping(finding["identity"], f"{label}.identity")
        _exact_keys(identity, {"path", "assertion"}, f"{label}.identity", {"symbol"})
        validate_relative_path(identity["path"], f"{label}.identity.path")
        _text(identity["assertion"], f"{label}.identity.assertion")
        if "symbol" in identity:
            _text(identity["symbol"], f"{label}.identity.symbol")
        status = _enum(
            finding["status"],
            {"new", "persisting", "resolved", "refuted"},
            f"{label}.status",
        )
        evidence = _list(finding["evidence"], f"{label}.evidence")
        if not evidence:
            raise ContractError(f"{label}.evidence must not be empty")
        for evidence_index, item in enumerate(evidence):
            _validate_evidence(item, f"{label}.evidence[{evidence_index}]")
        verification = _mapping(finding["verification"], f"{label}.verification")
        _exact_keys(
            verification,
            {"status", "counterevidence", "note"},
            f"{label}.verification",
            {"resulting_severity"},
        )
        verification_status = _enum(
            verification["status"],
            {"pending", "not-required", "confirmed", "downgraded", "refuted"},
            f"{label}.verification.status",
        )
        _text(verification["note"], f"{label}.verification.note", nonempty=False)
        if (
            "resulting_severity" in verification
            and verification["resulting_severity"] is not None
        ):
            _enum(
                verification["resulting_severity"],
                SEVERITIES,
                f"{label}.verification.resulting_severity",
            )
        counter = _list(
            verification["counterevidence"], f"{label}.verification.counterevidence"
        )
        for counter_index, item in enumerate(counter):
            _validate_evidence(
                item, f"{label}.verification.counterevidence[{counter_index}]"
            )
        if verification_status == "pending":
            if (
                not allow_provisional
                or severity not in {"high", "critical"}
                or status not in {"new", "persisting"}
            ):
                raise ContractError(
                    f"{label} pending verification is allowed only for active high/critical candidates"
                )
            if counter or verification.get("resulting_severity") is not None:
                raise ContractError(
                    f"{label} pending verification must not claim a result"
                )
        if verification_status in {"downgraded", "refuted"} and not counter:
            raise ContractError(
                f"{label} downgraded/refuted verification needs counterevidence"
            )
        if (status == "refuted") != (verification_status == "refuted"):
            raise ContractError(
                f"{label} finding and verification refuted statuses must match"
            )
        if verification_status == "downgraded":
            resulting_severity = verification.get("resulting_severity")
            if (
                resulting_severity is None
                or SEVERITY_RANK[resulting_severity] >= SEVERITY_RANK[severity]
            ):
                raise ContractError(
                    f"{label} downgraded verification needs a lower resulting severity"
                )
        elif verification.get("resulting_severity") not in (None, severity):
            raise ContractError(
                f"{label} resulting_severity must match severity unless downgraded"
            )
        if severity in {"high", "critical"} and verification_status == "not-required":
            raise ContractError(
                f"{label} high/critical finding needs adversarial verification"
            )
        if document["schema_version"] == 3:
            remediation = finding.get("remediation")
            if (
                status in {"new", "persisting"}
                and verification_status in {"confirmed", "downgraded"}
                and remediation is None
            ):
                raise ContractError(
                    f"{label}.remediation is required for a confirmed active finding"
                )
            if remediation is not None:
                remediation = _mapping(remediation, f"{label}.remediation")
                _exact_keys(
                    remediation,
                    {
                        "observable_effect",
                        "change_boundary",
                        "done_when",
                        "uncertainties",
                    },
                    f"{label}.remediation",
                )
                for key in ("observable_effect", "change_boundary", "done_when"):
                    _text(remediation[key], f"{label}.remediation.{key}")
                _unique_strings(
                    remediation["uncertainties"], f"{label}.remediation.uncertainties"
                )
    if previous is not None:
        old = validate_findings(previous)
        if old["auditor"] != document["auditor"]:
            raise ContractError("previous findings use a different auditor")
        current_by_id = {item["id"]: item for item in records}
        for item in old["findings"]:
            if item["id"] not in current_by_id:
                raise ContractError(f"previous finding id was removed: {item['id']}")
            current = current_by_id[item["id"]]
            if (current["kind"], current["identity"]) != (
                item["kind"],
                item["identity"],
            ):
                same_anchor = current["kind"] == item["kind"] and all(
                    current["identity"].get(key) == item["identity"].get(key)
                    for key in ("path", "symbol")
                )
                if same_anchor:
                    raise ContractError(
                        f"identity.assertion changed for persisting finding {item['id']}: "
                        "copy identity byte for byte from the previous run and put new wording in title or evidence detail"
                    )
                raise ContractError(f"previous finding id was reused: {item['id']}")
            if item["status"] == "refuted" and current["status"] in {
                "new",
                "persisting",
            }:
                raise ContractError(
                    f"refuted finding cannot return as active under the same id: {item['id']}"
                )
            if (
                item["status"] in {"new", "persisting"}
                and current["status"] == "resolved"
            ):
                path = current["identity"]["path"]
                old_scope = old["scope"]
                scope = document["scope"]
                if any(
                    not _covered_by(path, candidate["included"])
                    or _covered_by(
                        path, [*candidate["excluded"], *candidate["unscanned"]]
                    )
                    for candidate in (old_scope, scope)
                ):
                    raise ContractError(
                        f"resolved finding was outside comparable completed scope: {item['id']} (identity path: {path})"
                    )
    return document


def validate_inventory(value: Any, previous: Any | None = None) -> dict[str, Any]:
    inventory = _mapping(value, "inventory")
    _exact_keys(inventory, {"schema_version", "runs"}, "inventory")
    if inventory["schema_version"] == 3:
        return _validate_inventory_v3(inventory, previous)
    if type(inventory["schema_version"]) is not int or inventory["schema_version"] != 2:
        raise ContractError("inventory.schema_version must be 2")
    runs = _list(inventory["runs"], "inventory.runs")
    if not runs:
        raise ContractError("inventory.runs must not be empty")
    run_ids: set[str] = set()
    for index, raw in enumerate(runs):
        label = f"inventory.runs[{index}]"
        run = _mapping(raw, label)
        _exact_keys(
            run,
            {
                "id",
                "scanned_at",
                "revision",
                "worktree_clean",
                "source_state",
                "outcome",
                "domains",
                "coverage",
                "scope",
                "tools",
                "verification",
            },
            label,
        )
        run_id = _text(run["id"], f"{label}.id")
        if run_id in run_ids:
            raise ContractError(f"duplicate audit run id: {run_id}")
        run_ids.add(run_id)
        _timestamp(run["scanned_at"], f"{label}.scanned_at")
        revision = run["revision"]
        if revision is not None and (
            not isinstance(revision, str)
            or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision)
        ):
            raise ContractError(f"{label}.revision must be a Git object id or null")
        if (
            run["worktree_clean"] is not None
            and type(run["worktree_clean"]) is not bool
        ):
            raise ContractError(f"{label}.worktree_clean must be a boolean or null")
        _enum(run["source_state"], {"greenfield", "codebase"}, f"{label}.source_state")
        outcome = _enum(
            run["outcome"],
            {"complete", "coverage-incomplete", "failed"},
            f"{label}.outcome",
        )
        domains = _mapping(run["domains"], f"{label}.domains")
        _exact_keys(domains, {"ui", "data"}, f"{label}.domains")
        for name, state in domains.items():
            _enum(state, {"enabled", "absent", "unknown"}, f"{label}.domains.{name}")
        coverage = _mapping(run["coverage"], f"{label}.coverage")
        _exact_keys(
            coverage,
            {"required", "completed", "skipped", "failed"},
            f"{label}.coverage",
        )
        required = _unique_strings(
            coverage["required"], f"{label}.coverage.required", AUDITORS
        )
        completed = _unique_strings(
            coverage["completed"], f"{label}.coverage.completed", AUDITORS
        )
        failed = _unique_strings(
            coverage["failed"], f"{label}.coverage.failed", AUDITORS
        )
        skipped = _mapping(coverage["skipped"], f"{label}.coverage.skipped")
        for name, reason in skipped.items():
            _enum(name, AUDITORS, f"{label}.coverage.skipped key")
            _text(reason, f"{label}.coverage.skipped.{name}")
        if (
            (set(completed) & set(failed))
            or (set(completed) & set(skipped))
            or (set(failed) & set(skipped))
        ):
            raise ContractError(f"{label}.coverage auditor states overlap")
        expected_required = (
            {"greenfield"}
            if run["source_state"] == "greenfield"
            else BASE_AUDITORS
            | {name for name, state in domains.items() if state == "enabled"}
        )
        if set(required) != expected_required:
            raise ContractError(
                f"{label}.coverage.required does not match source state and domains"
            )
        if not set(completed) <= set(required) or not set(failed) <= set(required):
            raise ContractError(
                f"{label}.coverage completed/failed must be required auditors"
            )
        scope = _validate_scope(run["scope"], f"{label}.scope")
        tools = _mapping(run["tools"], f"{label}.tools")
        _exact_keys(tools, {"jcodemunch"}, f"{label}.tools")
        for name, state in tools.items():
            _enum(
                state,
                {"used", "unavailable", "skipped", "failed"},
                f"{label}.tools.{name}",
            )
        verification = _mapping(run["verification"], f"{label}.verification")
        _exact_keys(verification, {"blind", "issues"}, f"{label}.verification")
        blind = _enum(
            verification["blind"],
            {"passed", "failed", "not-run"},
            f"{label}.verification.blind",
        )
        issues = verification["issues"]
        if type(issues) is not int or issues < 0:
            raise ContractError(
                f"{label}.verification.issues must be a non-negative integer"
            )
        if blind == "failed" and issues == 0:
            raise ContractError(f"{label}.verification.failed needs at least one issue")
        if blind != "failed" and issues != 0:
            raise ContractError(
                f"{label}.verification.issues must be zero unless blind verification failed"
            )
        expected = (
            "failed"
            if failed or blind == "failed"
            else (
                "coverage-incomplete"
                if (
                    blind != "passed"
                    or scope["unscanned"]
                    or "unknown" in domains.values()
                    or set(completed) != set(required)
                )
                else "complete"
            )
        )
        if outcome != expected:
            raise ContractError(f"{label}.outcome must be {expected}")
    if previous is not None:
        old = validate_inventory(previous)
        old_runs = old["runs"]
        if len(runs) < len(old_runs) or runs[: len(old_runs)] != old_runs:
            raise ContractError("inventory history is not append-only")
    return inventory


def _validate_inventory_v3(
    inventory: dict[str, Any], previous: Any | None
) -> dict[str, Any]:
    runs = _list(inventory["runs"], "inventory.runs")
    if not runs:
        raise ContractError("inventory.runs must not be empty")
    seen: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(runs):
        label = f"inventory.runs[{index}]"
        run = _mapping(raw, label)
        _exact_keys(
            run,
            {
                "id",
                "scanned_at",
                "revision",
                "worktree_clean",
                "source_state",
                "outcome",
                "domains",
                "coverage",
                "scope",
                "source_tree",
                "tools",
                "verification",
                "results",
            },
            label,
        )
        run_id = _text(run["id"], f"{label}.id")
        if run_id in seen:
            raise ContractError(f"duplicate audit run id: {run_id}")
        _timestamp(run["scanned_at"], f"{label}.scanned_at")
        revision = run["revision"]
        if revision is not None and (
            not isinstance(revision, str)
            or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision)
        ):
            raise ContractError(f"{label}.revision must be a Git object id or null")
        if (
            run["worktree_clean"] is not None
            and type(run["worktree_clean"]) is not bool
        ):
            raise ContractError(f"{label}.worktree_clean must be a boolean or null")
        source_state = _enum(
            run["source_state"], {"greenfield", "codebase"}, f"{label}.source_state"
        )
        outcome = _enum(
            run["outcome"],
            {"complete", "coverage-incomplete", "failed"},
            f"{label}.outcome",
        )
        domains = _mapping(run["domains"], f"{label}.domains")
        _exact_keys(domains, {"ui", "data"}, f"{label}.domains")
        for name, state in domains.items():
            _enum(state, {"enabled", "absent", "unknown"}, f"{label}.domains.{name}")
        coverage = _mapping(run["coverage"], f"{label}.coverage")
        _exact_keys(
            coverage,
            {"required", "completed", "skipped", "failed"},
            f"{label}.coverage",
        )
        required = _unique_strings(
            coverage["required"], f"{label}.coverage.required", AUDITORS
        )
        completed = _unique_strings(
            coverage["completed"], f"{label}.coverage.completed", AUDITORS
        )
        failed = _unique_strings(
            coverage["failed"], f"{label}.coverage.failed", AUDITORS
        )
        skipped = _mapping(coverage["skipped"], f"{label}.coverage.skipped")
        for name, reason in skipped.items():
            _enum(name, AUDITORS, f"{label}.coverage.skipped key")
            _text(reason, f"{label}.coverage.skipped.{name}")
        if set(completed) & (set(failed) | set(skipped)) or set(failed) & set(skipped):
            raise ContractError(f"{label}.coverage auditor states overlap")
        expected_required = (
            {"greenfield"}
            if source_state == "greenfield"
            else BASE_AUDITORS
            | ({"performance"} if "performance" in required else set())
            | {name for name, state in domains.items() if state == "enabled"}
        )
        if (
            set(required) != expected_required
            or not set(completed) <= set(required)
            or not set(failed) <= set(required)
        ):
            raise ContractError(
                f"{label}.coverage does not match source state and domains"
            )
        scope = _validate_scope(run["scope"], f"{label}.scope")
        _scope_consistent(scope, f"{label}.scope")
        source_tree: dict[str, str] = {}
        for raw_source in _list(run["source_tree"], f"{label}.source_tree"):
            source = _mapping(raw_source, f"{label}.source_tree item")
            _exact_keys(source, {"path", "sha256"}, f"{label}.source_tree item")
            path = validate_relative_path(source["path"])
            if (
                path in source_tree
                or not isinstance(source["sha256"], str)
                or not HASH_RE.fullmatch(source["sha256"])
            ):
                raise ContractError(f"{label}.source_tree needs unique hashed paths")
            source_tree[path] = source["sha256"]
        if source_state == "codebase" and not source_tree:
            raise ContractError(f"{label}.source_tree cannot be empty for a codebase")
        tools = _mapping(run["tools"], f"{label}.tools")
        _exact_keys(tools, {"jcodemunch"}, f"{label}.tools")
        _enum(
            tools["jcodemunch"],
            {"used", "unavailable", "skipped", "failed"},
            f"{label}.tools.jcodemunch",
        )
        verification = _mapping(run["verification"], f"{label}.verification")
        _exact_keys(verification, {"blind", "issues"}, f"{label}.verification")
        blind = _enum(
            verification["blind"],
            {"passed", "failed", "not-run"},
            f"{label}.verification.blind",
        )
        issues = verification["issues"]
        if type(issues) is not int or issues < 0 or (blind == "failed") != (issues > 0):
            raise ContractError(f"{label}.verification issues do not match blind state")
        results = _mapping(run["results"], f"{label}.results")
        if set(results) != set(completed):
            raise ContractError(
                f"{label}.results must contain exactly the completed auditors"
            )
        for auditor, raw_result in results.items():
            result = _mapping(raw_result, f"{label}.results.{auditor}")
            _exact_keys(
                result,
                {
                    "origin",
                    "source_run_id",
                    "scanned_at",
                    "sha256",
                    "scope",
                    "sources",
                    "covered_paths",
                },
                f"{label}.results.{auditor}",
            )
            source_run = _text(
                result["source_run_id"], f"{label}.results.{auditor}.source_run_id"
            )
            origin = _enum(
                result["origin"],
                {"current", "reused"},
                f"{label}.results.{auditor}.origin",
            )
            if (origin == "current") != (source_run == run_id):
                raise ContractError(
                    f"{label}.results.{auditor}.origin contradicts source run"
                )
            _timestamp(result["scanned_at"], f"{label}.results.{auditor}.scanned_at")
            if not isinstance(result["sha256"], str) or not HASH_RE.fullmatch(
                result["sha256"]
            ):
                raise ContractError(f"{label}.results.{auditor}.sha256 is invalid")
            result_scope = _validate_scope(
                result["scope"], f"{label}.results.{auditor}.scope"
            )
            _scope_consistent(result_scope, f"{label}.results.{auditor}.scope")
            if any(
                set(result_scope[key]) != set(scope[key])
                for key in ("included", "excluded", "unscanned")
            ):
                raise ContractError(
                    f"{label}.results.{auditor}.scope contradicts inventory scope"
                )
            covered = _unique_strings(
                result["covered_paths"], f"{label}.results.{auditor}.covered_paths"
            )
            if source_state == "codebase" and not covered:
                raise ContractError(
                    f"{label}.results.{auditor} cannot claim completed codebase coverage without covered paths"
                )
            for path in covered:
                validate_relative_path(path, f"{label}.results.{auditor}.covered_paths")
                if path not in source_tree:
                    raise ContractError(
                        f"{label}.results.{auditor} covered path is absent from source_tree: {path}"
                    )
                if not _covered_by(path, scope["included"]) or _covered_by(
                    path, [*scope["excluded"], *scope["unscanned"]]
                ):
                    raise ContractError(
                        f"{label}.results.{auditor} covered path is outside completed scope: {path}"
                    )
            source_paths: set[str] = set()
            for source_index, raw_source in enumerate(
                _list(result["sources"], f"{label}.results.{auditor}.sources")
            ):
                source = _mapping(
                    raw_source, f"{label}.results.{auditor}.sources[{source_index}]"
                )
                _exact_keys(
                    source,
                    {"path", "sha256"},
                    f"{label}.results.{auditor}.sources[{source_index}]",
                )
                path = validate_relative_path(source["path"])
                if path in source_paths or path not in covered:
                    raise ContractError(
                        f"{label}.results.{auditor}.sources must be unique covered paths"
                    )
                source_paths.add(path)
                if not isinstance(source["sha256"], str) or not HASH_RE.fullmatch(
                    source["sha256"]
                ):
                    raise ContractError(
                        f"{label}.results.{auditor}.sources hash is invalid"
                    )
                if source["sha256"] != source_tree[path]:
                    raise ContractError(
                        f"{label}.results.{auditor} source hash differs from source_tree: {path}"
                    )
            if source_paths != set(covered):
                raise ContractError(
                    f"{label}.results.{auditor}.sources must hash every covered path"
                )
            if source_run != run_id:
                old = seen.get(source_run)
                if old is None or {
                    key: value
                    for key, value in old["results"].get(auditor, {}).items()
                    if key != "origin"
                } != {key: value for key, value in result.items() if key != "origin"}:
                    raise ContractError(
                        f"{label}.results.{auditor} reused result lacks identical historical provenance"
                    )
        source_hashes: dict[str, str] = {}
        for result in results.values():
            for source in result["sources"]:
                path, source_hash = source["path"], source["sha256"]
                if path in source_hashes and source_hashes[path] != source_hash:
                    raise ContractError(
                        f"{label}.results disagree on source hash: {path}"
                    )
                source_hashes[path] = source_hash
        expected = (
            "failed"
            if failed
            else "coverage-incomplete"
            if (
                set(completed) != set(required)
                or scope["unscanned"]
                or "unknown" in domains.values()
                or {
                    path
                    for path in source_tree
                    if _covered_by(path, scope["included"])
                    and not _covered_by(path, [*scope["excluded"], *scope["unscanned"]])
                }
                - set(source_hashes)
            )
            else "complete"
        )
        if outcome != expected:
            raise ContractError(
                f"{label}.outcome must be {expected}; document verification is separate"
            )
        seen[run_id] = run
    if previous is not None:
        old = validate_inventory(previous)
        if (
            old["schema_version"] != 3
            or len(runs) < len(old["runs"])
            or runs[: len(old["runs"])] != old["runs"]
        ):
            raise ContractError("inventory v3 history is not append-only")
    return inventory


def _scope_consistent(scope: dict[str, Any], label: str) -> None:
    if not scope["included"]:
        raise ContractError(f"{label}.included must not be empty")
    for path in scope["included"]:
        if _covered_by(path, [*scope["excluded"], *scope["unscanned"]]):
            raise ContractError(f"{label}.included is excluded or unscanned: {path}")
    for path in scope["excluded"]:
        if _covered_by(path, scope["unscanned"]) or any(
            _covered_by(other, [path]) for other in scope["unscanned"]
        ):
            raise ContractError(f"{label}.excluded and unscanned overlap: {path}")


def validate_project_map(value: Any) -> dict[str, Any]:
    document = _mapping(value, "project map")
    _exact_keys(document, {"schema_version", "run_id", "nodes", "edges"}, "project map")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ContractError("project_map.schema_version must be 1")
    _text(document["run_id"], "project_map.run_id")
    nodes = _list(document["nodes"], "project_map.nodes")
    node_ids: set[str] = set()
    for index, raw in enumerate(nodes):
        label = f"project_map.nodes[{index}]"
        node = _mapping(raw, label)
        _exact_keys(
            node, {"id", "label", "kind", "status", "evidence"}, label, {"group"}
        )
        node_id = _text(node["id"], f"{label}.id")
        if not ID_RE.fullmatch(node_id) or node_id in node_ids:
            raise ContractError(f"{label}.id must be a unique lowercase identifier")
        node_ids.add(node_id)
        _text(node["label"], f"{label}.label")
        if "group" in node:
            _text(node["group"], f"{label}.group")
        _enum(node["kind"], PROJECT_MAP_KINDS, f"{label}.kind")
        _enum(node["status"], PROJECT_MAP_STATUSES, f"{label}.status")
        evidence = _list(node["evidence"], f"{label}.evidence")
        if not evidence:
            raise ContractError(f"{label}.evidence must not be empty")
        for evidence_index, item in enumerate(evidence):
            _validate_evidence(item, f"{label}.evidence[{evidence_index}]")
    edges = _list(document["edges"], "project_map.edges")
    seen_edges: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(edges):
        label = f"project_map.edges[{index}]"
        edge = _mapping(raw, label)
        _exact_keys(edge, {"from", "to", "label", "evidence"}, label)
        source = _text(edge["from"], f"{label}.from")
        target = _text(edge["to"], f"{label}.to")
        relation = _text(edge["label"], f"{label}.label")
        if source not in node_ids or target not in node_ids:
            raise ContractError(f"{label} references an unknown node")
        identity = (source, target, relation)
        if identity in seen_edges:
            raise ContractError(f"{label} duplicates an existing edge")
        seen_edges.add(identity)
        evidence = _list(edge["evidence"], f"{label}.evidence")
        if not evidence:
            raise ContractError(f"{label}.evidence must not be empty")
        for evidence_index, item in enumerate(evidence):
            _validate_evidence(item, f"{label}.evidence[{evidence_index}]")
    return document


def _artifact_allowed(path: str, kind: str) -> bool:
    if path in {CONFIG_PATH, MANIFEST_PATH}:
        return False
    if kind == "owned_file":
        return path in {"PROJECT_CONTEXT.md", ".jcodemunch.jsonc"} or path.startswith(
            "repodocs/"
        )
    if kind == "managed_block":
        return path in HOST_FILES.values()
    return False


def validate_manifest(value: Any) -> dict[str, Any]:
    manifest = _mapping(value, "manifest")
    schema = manifest.get("schema_version")
    if type(schema) is not int or schema not in {1, 2}:
        raise ContractError("manifest.schema_version must be 1 or 2")
    profile = (
        _enum(manifest.get("profile"), {"audit", "context"}, "manifest.profile")
        if schema == 2
        else "context"
    )
    required = {
        "schema_version",
        "skill_version",
        "config_sha256",
        "domains",
        "hosts",
        "artifacts",
    }
    if schema == 2:
        required |= {"profile", "context_run_id", "reserved_ids"}
        if (profile == "audit") != (manifest["context_run_id"] is None):
            raise ContractError(
                "manifest.context_run_id must be null for audit and set for context"
            )
        if profile == "context":
            _text(manifest["context_run_id"], "manifest.context_run_id")
        seen_reserved: set[str] = set()
        for raw in _list(manifest["reserved_ids"], "manifest.reserved_ids"):
            item = _mapping(raw, "manifest reserved id")
            _exact_keys(item, {"id", "title_sha256"}, "manifest reserved id")
            identifier = _text(item["id"], "manifest reserved id")
            if (
                not re.fullmatch(r"(?:ADR|MB)-[0-9]{3,}", identifier)
                or identifier in seen_reserved
                or not isinstance(item["title_sha256"], str)
                or not HASH_RE.fullmatch(item["title_sha256"])
            ):
                raise ContractError("manifest reserved id or title hash is invalid")
            seen_reserved.add(identifier)
    _exact_keys(
        manifest,
        required,
        "manifest",
    )
    version = _text(manifest["skill_version"], "manifest.skill_version")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ContractError("manifest.skill_version must be semantic version x.y.z")
    if not isinstance(manifest["config_sha256"], str) or not HASH_RE.fullmatch(
        manifest["config_sha256"]
    ):
        raise ContractError("manifest.config_sha256 must be sha256:<64 lowercase hex>")
    domains = _unique_strings(manifest["domains"], "manifest.domains", ALL_DOMAINS)
    if profile == "context" and not CORE_DOMAINS <= set(domains):
        raise ContractError(
            "manifest.domains must include architecture, stack, security, and testing"
        )
    if profile == "audit" and domains:
        raise ContractError("audit profile has no generated policy domains")
    _unique_strings(manifest["hosts"], "manifest.hosts", set(HOST_FILES))
    if profile == "audit" and manifest["hosts"]:
        raise ContractError("audit profile has no managed host blocks")
    artifacts = _list(manifest["artifacts"], "manifest.artifacts")
    if not artifacts:
        raise ContractError("manifest.artifacts must not be empty")
    ids: set[str] = set()
    paths: set[str] = set()
    for index, raw in enumerate(artifacts):
        label = f"manifest.artifacts[{index}]"
        artifact = _mapping(raw, label)
        _exact_keys(
            artifact,
            {"id", "path", "kind", "sha256"},
            label,
            {"sections"} if schema == 2 else set(),
        )
        artifact_id = _text(artifact["id"], f"{label}.id")
        if not ID_RE.fullmatch(artifact_id) or artifact_id in ids:
            raise ContractError(f"{label}.id must be unique lowercase identifier")
        ids.add(artifact_id)
        path = validate_relative_path(artifact["path"], f"{label}.path")
        if path in paths:
            raise ContractError(f"duplicate manifest artifact path: {path}")
        paths.add(path)
        kind = _enum(artifact["kind"], ARTIFACT_KINDS, f"{label}.kind")
        if not _artifact_allowed(path, kind):
            raise ContractError(f"{label} path is outside the generated surface")
        if not isinstance(artifact["sha256"], str) or not HASH_RE.fullmatch(
            artifact["sha256"]
        ):
            raise ContractError(f"{label}.sha256 must be sha256:<64 lowercase hex>")
        if (
            schema == 2
            and profile == "context"
            and kind == "owned_file"
            and path.endswith(".md")
            and not path.startswith("repodocs/audit/reports/")
            and not artifact.get("sections")
        ):
            raise ContractError(
                f"{label}.sections must describe policy document provenance"
            )
        if "sections" in artifact:
            if (
                kind != "owned_file"
                or not path.endswith(".md")
                or path.startswith("repodocs/audit/reports/")
            ):
                raise ContractError(
                    f"{label}.sections is only for owned policy Markdown"
                )
            section_ids: set[str] = set()
            for section_index, raw_section in enumerate(
                _list(artifact["sections"], f"{label}.sections")
            ):
                section = _mapping(raw_section, f"{label}.sections[{section_index}]")
                _exact_keys(
                    section,
                    {
                        "id",
                        "source_run_id",
                        "covered_paths",
                        "coverage_sha256",
                        "sources",
                    },
                    f"{label}.sections[{section_index}]",
                )
                section_id = _text(
                    section["id"], f"{label}.sections[{section_index}].id"
                )
                if section_id in section_ids:
                    raise ContractError(
                        f"{label}.sections has duplicate id: {section_id}"
                    )
                section_ids.add(section_id)
                _text(
                    section["source_run_id"],
                    f"{label}.sections[{section_index}].source_run_id",
                )
                covered = _unique_strings(
                    section["covered_paths"],
                    f"{label}.sections[{section_index}].covered_paths",
                )
                for source_path in covered:
                    validate_relative_path(source_path)
                expected_hash = sha256_bytes(
                    json.dumps(
                        sorted(covered), ensure_ascii=False, separators=(",", ":")
                    ).encode("utf-8")
                )
                if section["coverage_sha256"] != expected_hash:
                    raise ContractError(
                        f"{label}.sections[{section_index}].coverage_sha256 does not match covered paths"
                    )
                source_paths: set[str] = set()
                for source in _list(
                    section["sources"], f"{label}.sections[{section_index}].sources"
                ):
                    source = _mapping(source, f"{label}.section source")
                    _exact_keys(source, {"path", "sha256"}, f"{label}.section source")
                    source_path = validate_relative_path(source["path"])
                    if (
                        source_path in source_paths
                        or source_path not in covered
                        or not isinstance(source["sha256"], str)
                        or not HASH_RE.fullmatch(source["sha256"])
                    ):
                        raise ContractError(
                            f"{label}.section source must be a hashed covered path"
                        )
                    source_paths.add(source_path)
                if source_paths != set(covered):
                    raise ContractError(
                        f"{label}.section sources must hash every covered path"
                    )
    context = next((item for item in artifacts if item["id"] == "context"), None)
    if profile == "context" and (
        context is None
        or context["path"] != "PROJECT_CONTEXT.md"
        or context["kind"] != "owned_file"
    ):
        raise ContractError(
            "manifest context id must map to the owned PROJECT_CONTEXT.md"
        )
    if profile == "audit" and context is not None:
        raise ContractError("audit profile must not own PROJECT_CONTEXT.md")
    return manifest


def _host_block(host: str) -> str:
    try:
        filename = "CLAUDE.block.md" if host == "claude" else "AGENTS.block.md"
        raw = (
            Path(__file__).resolve().parents[1] / "templates/host" / filename
        ).read_text(encoding="utf-8")
    except (KeyError, OSError) as exc:
        raise ContractError(f"cannot load {host} host block") from exc
    return raw.rstrip("\n")


def extract_host_block(text: str, host: str) -> str | None:
    if host not in HOST_MARKERS:
        raise ContractError(f"unknown host: {host}")
    begin, end = HOST_MARKERS[host]
    if begin not in text and end not in text:
        return None
    if text.count(begin) != 1 or text.count(end) != 1:
        raise ContractError(f"{host} managed block markers must occur exactly once")
    start = text.index(begin)
    end_index = text.index(end)
    if end_index < start:
        raise ContractError(f"{host} managed block markers are out of order")
    return text[start : end_index + len(end)]


def merge_host_text(text: str, host: str, expected_sha256: str | None = None) -> str:
    """Insert or replace one host block without changing any outside byte."""
    if expected_sha256 is not None:
        if not HASH_RE.fullmatch(expected_sha256):
            raise ContractError("expected-sha256 is not a valid sha256:<hex> value")
        if sha256_text(text) != expected_sha256:
            raise ContractError("host input changed since preview")
    block = _host_block(host)
    current = extract_host_block(text, host)
    if current is not None:
        start = text.index(current)
        return text[:start] + block + text[start + len(current) :]
    if not text:
        return block + "\n"
    separator = "\n" if text.startswith("\n") else "\n\n"
    return block + separator + text


def apply_host_file(
    repo: Path, host: str, expected_sha256: str | None, *, allow_create: bool = False
) -> str:
    """Apply a reviewed host merge without replacing the file on failure."""
    root = _resolve_existing(repo, "repository path")
    target = safe_path(root, HOST_FILES[host])
    exists = os.path.lexists(target)
    if (exists and allow_create) or (not exists and not allow_create):
        raise ContractError("--allow-create is only for a missing host file")
    if exists and expected_sha256 is None:
        raise ContractError(
            "--expected-sha256 is required when applying to an existing host file"
        )
    if not exists and expected_sha256 is not None:
        raise ContractError("--expected-sha256 is not used when creating a host file")
    raw = _read_regular(target, target.name) if exists else b""
    if exists and sha256_bytes(raw) != expected_sha256:
        raise ContractError("host input changed since preview")
    text = _decode_text(raw, target.name)
    merged = merge_host_text(text, host)
    bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
    candidate = bom + merged.encode("utf-8")
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{target.name}.", dir=target.parent
        )
        with os.fdopen(descriptor, "wb") as output:
            output.write(candidate)
            output.flush()
            os.fsync(output.fileno())
        if exists:
            os.chmod(temporary, stat.S_IMODE(os.lstat(target).st_mode))
        safe_path(root, HOST_FILES[host])
        if os.path.lexists(target) != exists or (
            exists and _read_regular(target, target.name) != raw
        ):
            raise ContractError("host input changed since preview")
        os.replace(temporary, target)
        temporary = None
    except OSError as exc:
        raise ContractError(f"cannot apply host file: {exc.strerror or exc}") from exc
    finally:
        if temporary is not None and os.path.lexists(temporary):
            os.unlink(temporary)
    return sha256_bytes(candidate)


def _decode_text(raw: bytes, label: str) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError(f"{label} is not UTF-8") from exc


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", str(root), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContractError(f"cannot inspect Git repository: {exc}") from exc


def _only_managed_host_content(path: Path, host: str) -> bool:
    try:
        text = _decode_text(_read_regular(path, path.name), path.name)
        block = extract_host_block(text, host)
    except ContractError:
        return False
    return (
        block is not None
        and not (
            text[: text.index(block)] + text[text.index(block) + len(block) :]
        ).strip()
    )


def _verified_generated_paths(root: Path) -> set[str]:
    """Trust generated ownership only when the complete manifest still validates."""
    try:
        validate_project(root)
        manifest = validate_manifest(load_json(root / MANIFEST_PATH))
    except ContractError:
        return set()
    return {
        CONFIG_PATH,
        MANIFEST_PATH,
        *(artifact["path"] for artifact in manifest["artifacts"]),
    }


def _source_evidence(root: Path, generated_files: set[str] | None = None) -> list[str]:
    evidence: list[str] = []
    if generated_files is None:
        generated_files = _verified_generated_paths(root)
    ignored_trees = {".git"}
    ignored_prefixes = {
        ".agents/skills/project-context",
        ".claude/skills/project-context",
    }
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        rel_dir = Path(directory).relative_to(root).as_posix()
        if rel_dir == ".":
            rel_dir = ""
        kept: list[str] = []
        for name in names:
            rel = f"{rel_dir}/{name}".lstrip("/")
            if name in ignored_trees and not rel_dir:
                continue
            if any(
                rel == prefix or rel.startswith(prefix + "/")
                for prefix in ignored_prefixes
            ):
                continue
            if _is_symlink_or_junction(Path(directory) / name):
                evidence.append(rel)
                if len(evidence) == 20:
                    return sorted(evidence)
                continue
            kept.append(name)
        names[:] = kept
        for name in files:
            rel = f"{rel_dir}/{name}".lstrip("/")
            path = Path(directory) / name
            if rel == ".git":  # Linked worktrees use a root .git control file.
                continue
            if any(rel.startswith(prefix + "/") for prefix in ignored_prefixes):
                continue
            if _is_symlink_or_junction(path):
                evidence.append(rel)
                if len(evidence) == 20:
                    return sorted(evidence)
                continue
            if rel in generated_files:
                host = next(
                    (name for name, filename in HOST_FILES.items() if filename == rel),
                    None,
                )
                if host is None or _only_managed_host_content(path, host):
                    continue
            if not rel_dir and name in {
                ".DS_Store",
                ".gitattributes",
                ".gitignore",
                "CODE_OF_CONDUCT.md",
            }:
                continue
            if not rel_dir and name in {
                "LICENSE",
                "LICENSE.md",
                "LICENSE.txt",
                "LICENCE",
                "LICENCE.md",
                "LICENCE.txt",
            }:
                continue
            if not rel_dir and name.startswith("README"):
                try:
                    if not path.read_bytes().strip():
                        continue
                except OSError:
                    pass
            evidence.append(rel)
            if len(evidence) == 20:
                return sorted(evidence)
    return sorted(evidence)


def _source_worktree_clean(root: Path) -> bool | None:
    try:
        result = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    except ContractError:
        return None
    if result.returncode != 0:
        return None
    generated = _verified_generated_paths(root)
    ignored_prefixes = (
        ".agents/skills/project-context",
        ".claude/skills/project-context",
    )
    for line in result.stdout.splitlines():
        path = line[3:] if len(line) >= 4 else line
        paths = path.split(" -> ") if " -> " in path else [path]
        for candidate in paths:
            if any(
                candidate == prefix or candidate.startswith(prefix + "/")
                for prefix in ignored_prefixes
            ):
                continue
            if candidate in generated:
                host = next(
                    (
                        name
                        for name, filename in HOST_FILES.items()
                        if filename == candidate
                    ),
                    None,
                )
                if host is not None and _host_user_content_changed(
                    root, candidate, host
                ):
                    return False
                continue
            return False
    return True


def _host_user_content_changed(root: Path, relative: str, host: str) -> bool:
    path = root / relative
    if not path.is_file():
        return True
    try:
        current_raw = _read_regular(path, relative)
        current = _decode_text(current_raw, relative)
    except ContractError:
        return True
    base = _git(root, "show", f"HEAD:{relative}")
    previous_has_bom = base.returncode == 0 and base.stdout.startswith("\ufeff")
    if current_raw.startswith(b"\xef\xbb\xbf") != previous_has_bom:
        return True
    try:
        previous = base.stdout.removeprefix("\ufeff") if base.returncode == 0 else ""
        expected = merge_host_text(previous, host)
    except ContractError:
        return True
    current = current.replace("\r\n", "\n").replace("\r", "\n")
    expected = expected.replace("\r\n", "\n").replace("\r", "\n")
    return current != expected


def _is_symlink_or_junction(path: Path) -> bool:
    """NTFS junctions are reparse points that Path.is_symlink() does not report; every
    symlink guard in this file must treat them the same way, or Windows silently passes
    what macOS and Linux reject."""
    if path.is_symlink():
        return True
    if os.name == "nt":
        is_junction = getattr(path, "is_junction", None)
        if is_junction is not None:  # Python 3.12+
            try:
                return bool(is_junction())
            except OSError:
                return False
        try:
            reparse_tag = getattr(os.lstat(path), "st_reparse_tag", 0)
        except OSError:
            return False
        return reparse_tag == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)
    return False


def _resolve_existing(path: Path, label: str) -> Path:
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise ContractError(f"{label} does not exist: {path}") from exc


def _context_state(root: Path) -> tuple[str, str | None]:
    repodocs = root / "repodocs"
    if _is_symlink_or_junction(repodocs):
        return "invalid", "repodocs: symlink is not allowed in generated path"
    has_repodocs = repodocs.is_dir() and any(repodocs.iterdir())
    has_managed_host = False
    host_problem: str | None = None
    for host, relative in HOST_FILES.items():
        path = root / relative
        if os.path.lexists(path):
            try:
                text = _decode_text(_read_regular(path, relative), relative)
                has_managed_host = (
                    has_managed_host or extract_host_block(text, host) is not None
                )
            except ContractError as exc:
                # A broken host file (symlink, non-UTF-8, malformed markers) is a reportable
                # invalid state, never a crash - the dashboard must be able to show it.
                host_problem = f"{relative}: {str(exc).replace(str(root), '<repo>')}"
    try:
        has_surface = (
            has_repodocs
            or has_managed_host
            or any(
                _lexists_in_safe_parent(root, relative)
                for relative in (CONFIG_PATH, MANIFEST_PATH, "PROJECT_CONTEXT.md")
            )
        )
    except ContractError as exc:
        return "invalid", str(exc).replace(str(root), "<repo>")
    if host_problem is not None:
        return ("invalid", host_problem) if has_surface else ("absent", None)
    if not has_surface:
        return "absent", None
    try:
        validate_project(root)
    except ContractError as exc:
        return "invalid", str(exc).replace(str(root), "<repo>")
    return "valid", None


_INSTRUCTION_BASENAMES = {
    "agents.md",
    "claude.md",
    "claude.local.md",
    "agents.project-context.md",
}
_INSTRUCTION_CONFIG_FILES = {
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".claude/hooks.json",
    ".claude/mcp.json",
    ".codex/config.toml",
    ".codex/hooks.json",
    ".cursor/settings.json",
    ".cursor/hooks.json",
    ".cursor/mcp.json",
}
_SKILLS_ROOTS = (
    ".github/skills/",
    ".claude/skills/",
    ".agents/skills/",
    ".codex/skills/",
    ".cursor/skills/",
)


def _is_agent_instruction(path: str) -> bool:
    lowered = path.lower()
    if lowered.rsplit("/", 1)[-1] in _INSTRUCTION_BASENAMES:
        return True
    if lowered in _INSTRUCTION_CONFIG_FILES:
        return True
    if (
        lowered.startswith((".cursor/rules/", ".cursor/commands/"))
        or lowered == ".cursor/commands"
    ):
        return True
    for skills_root in _SKILLS_ROOTS:
        if lowered.startswith(skills_root) and lowered.endswith(".md"):
            # One logical unit per vendored skill: its SKILL.md (any depth) or a file at the
            # skills root. Internal reference files would flood the map fifty entries deep;
            # their divergence still surfaces through the skill-directory aggregate hash.
            remainder = lowered[len(skills_root) :]
            return remainder.rsplit("/", 1)[-1] == "skill.md" or "/" not in remainder
    return False


def _instruction_hosts(path: str) -> list[str]:
    lowered = path.lower()
    basename = lowered.rsplit("/", 1)[-1]
    hosts: list[str] = []
    # Basename rules apply only outside other hosts' directories: .cursor/commands/agents.md
    # is a cursor command that happens to be named agents, not a codex file.
    foreign = lowered.startswith((".cursor/", ".github/"))
    if (
        basename in {"claude.md", "claude.local.md"} and not foreign
    ) or lowered.startswith(".claude/"):
        hosts.append("claude")
    if (
        basename in {"agents.md", "agents.project-context.md"} and not foreign
    ) or lowered.startswith((".codex/", ".agents/")):
        hosts.append("codex")
    if lowered.startswith(".cursor/"):
        hosts.append("cursor")
    if lowered.startswith(".github/"):
        hosts.append("github")
    return hosts or ["unknown"]


def _skill_directory_digest(root: Path, skill_md: str) -> tuple[str | None, list[str]]:
    """Aggregate hash over a vendored skill directory, so divergence in any of its files
    (not only SKILL.md) surfaces on the one entry that represents the skill."""
    lowered = skill_md.lower()
    for skills_root in _SKILLS_ROOTS:
        if lowered.startswith(skills_root) and lowered.rsplit("/", 1)[-1] == "skill.md":
            skill_dir = (root / skill_md).parent
            lines: list[str] = []
            skipped: list[str] = []
            for directory, dirnames, filenames in os.walk(skill_dir, followlinks=False):
                safe_dirs = []
                for name in sorted(dirnames):
                    path = Path(directory) / name
                    try:
                        safe_path(root, path.relative_to(root).as_posix())
                    except ContractError:
                        skipped.append(path.relative_to(root).as_posix())
                    else:
                        safe_dirs.append(name)
                dirnames[:] = safe_dirs
                for name in sorted(filenames):
                    file_path = Path(directory) / name
                    path_label = file_path.relative_to(root).as_posix()
                    try:
                        safe_path(root, path_label)
                    except ContractError:
                        skipped.append(path_label)
                        continue
                    if not file_path.is_file():
                        continue
                    relative = file_path.relative_to(skill_dir).as_posix()
                    try:
                        lines.append(
                            f"{relative}:{sha256_bytes(_read_regular(file_path, relative))}"
                        )
                    except ContractError:
                        skipped.append(path_label)
                        continue
                    if len(lines) == 400:
                        return sha256_text("\n".join([*lines, "truncated"])), skipped
            return sha256_text("\n".join(lines)), skipped
    return None, []


def _agent_instruction_map(root: Path) -> tuple[list[dict[str, Any]], bool]:
    """Inventory every file that can instruct an agent. Discovery honours the repository's
    own ignore rules, except the root host files and host-configuration directories, which
    are always checked - an ignore rule must never hide an instruction file from the map."""
    try:
        tracked_result = _git(root, "ls-files", "-z")
        others_result = _git(root, "ls-files", "-z", "--others", "--exclude-standard")
    except ContractError:
        return [], True
    if tracked_result.returncode != 0 or others_result.returncode != 0:
        # git ran but the listing failed (corrupt index, unreadable object store):
        # discovery is unknown, not empty - the empty-set fallback would render a
        # silently partial inventory as verified-clean.
        return [], True
    tracked = {p for p in tracked_result.stdout.split("\0") if p}
    others = {p for p in others_result.stdout.split("\0") if p}
    # Git-ignored instruction files are still loaded by the hosts (ignoring CLAUDE.local.md is
    # the documented recommendation), so they must not hide from the map. The pathspecs bound
    # the listing to instruction basenames - a bare --ignored listing would return node_modules.
    try:
        ignored_result = _git(
            root,
            "ls-files",
            "-z",
            "--others",
            "--ignored",
            "--exclude-standard",
            "--",
            ":(icase)*agents.md",
            ":(icase)*claude.md",
            ":(icase)*claude.local.md",
            ":(icase)*agents.project-context.md",
            # Dependencies increasingly ship their own CLAUDE.md/AGENTS.md; vendor trees are a
            # listing bound here (they would evict repo-local entries from the 200 cap), not an
            # audit-scope decision.
            ":(exclude)node_modules/",
            ":(exclude)vendor/",
            ":(exclude)third_party/",
        )
    except ContractError:
        ignored_result = None
    ignored = (
        {p for p in ignored_result.stdout.split("\0") if p}
        if ignored_result is not None and ignored_result.returncode == 0
        else set()
    )
    candidates = {p for p in tracked | others | ignored if _is_agent_instruction(p)}
    # Root host files by exact directory listing, so a git-ignored CLAUDE.md is still seen.
    try:
        candidates.update(
            name for name in os.listdir(root) if name.lower() in _INSTRUCTION_BASENAMES
        )
    except OSError:
        pass
    for extra in (".claude", ".agents", ".codex", ".cursor", ".github"):
        base = root / extra
        if base.is_dir() and not _is_symlink_or_junction(base):
            for directory, dirnames, filenames in os.walk(base, followlinks=False):
                dirnames[:] = [
                    d
                    for d in dirnames
                    if not _is_symlink_or_junction(Path(directory) / d)
                ]
                for name in filenames:
                    file_path = Path(directory) / name
                    if _is_symlink_or_junction(file_path):
                        continue
                    relative = file_path.relative_to(root).as_posix()
                    if _is_agent_instruction(relative):
                        candidates.add(relative)
    # The discovery listing above is bounded to basenames; the flag must be accurate for
    # every candidate (a git-ignored .claude/settings.local.json arrives via the host walk).
    untracked_candidates = sorted(candidates - tracked)
    if untracked_candidates:
        try:
            check_result = _git(
                root,
                "-c",
                "core.excludesfile=",
                "check-ignore",
                "-z",
                "--",
                *untracked_candidates,
            )
            if check_result.returncode in (0, 1):
                ignored = {p for p in check_result.stdout.split("\0") if p}
        except ContractError:
            pass
    entries: list[dict[str, Any]] = []
    truncated = False
    for relative in sorted(candidates):
        if len(entries) == 200:
            truncated = True
            break
        try:
            path = safe_path(root, relative)
        except ContractError:
            entries.append(
                {
                    "path": relative,
                    "tracked": relative in tracked,
                    "ignored": relative in ignored,
                    "size": 0,
                    "sha256": "",
                    "modified": "",
                    "hosts": _instruction_hosts(relative),
                    "skipped_paths": [relative],
                }
            )
            continue
        if not path.is_file() or _is_symlink_or_junction(path):
            continue
        try:
            raw = _read_regular(path, relative)
            modified = (
                datetime.fromtimestamp(path.lstat().st_mtime, timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z")
            )
        except (ContractError, OSError):
            continue
        digest, skipped_paths = _skill_directory_digest(root, relative)
        entries.append(
            {
                "path": relative,
                "tracked": relative in tracked,
                "ignored": relative in ignored,
                "size": len(raw),
                "sha256": digest or sha256_bytes(raw),
                "modified": modified,
                "hosts": _instruction_hosts(relative),
                "skipped_paths": skipped_paths,
            }
        )
    return entries, truncated


def _scope_review(root: Path, exclusions: list[str]) -> list[dict[str, Any]]:
    """Cross-check configured exclusions against git without changing what gets scanned."""
    review: list[dict[str, Any]] = []
    for excluded in exclusions:
        try:
            # :(literal) disables pathspec magic and globbing, matching _covered_by's
            # literal semantics; validate_config also rejects glob/magic characters.
            ls_result = _git(root, "ls-files", "-z", "--", f":(literal){excluded}")
            tracked_files = (
                [p for p in ls_result.stdout.split("\0") if p]
                if ls_result.returncode == 0
                else []
            )
            # --no-index: pure pattern matching, so a TRACKED path that ignore rules also match
            # is reported - that combination is a no-op ignore and usually a forgotten git rm --cached.
            # The empty core.excludesfile neutralises the user's personal global ignores: only the
            # repository's own .gitignore and .git/info/exclude count as an inconsistency.
            ignored = (
                _git(
                    root,
                    "-c",
                    "core.excludesfile=",
                    "check-ignore",
                    "-q",
                    "--no-index",
                    "--",
                    excluded,
                ).returncode
                == 0
            )
        except ContractError:
            continue
        review.append(
            {
                "path": excluded,
                "tracked_files": len(tracked_files),
                "tracked_and_ignored": bool(tracked_files) and ignored,
                "agent_instruction_files": sorted(
                    p for p in tracked_files if _is_agent_instruction(p)
                )[:20],
            }
        )
    return review


_DECISION_ID_RE = re.compile(r"(?<![A-Za-z0-9])(ADR|MB)-[0-9]+(?![0-9])")


def _decision_citations(root: Path) -> list[dict[str, Any]]:
    """ADR/MB citations in tracked project sources, excluding sample/test literals."""
    try:
        # -z separates the path from the matched line with NUL, so paths containing ':' parse
        # exactly; ids are re-extracted in Python with word boundaries, so prose like
        # "512MB-4GB" or "LOADR-9" never registers as an occupied decision id.
        result = _git(
            root,
            "grep",
            "-I",
            "-z",
            "-E",
            r"(ADR|MB)-[0-9]+",
            "--",
            ".",
            ":(exclude)repodocs",
        )
    except ContractError:
        return []
    if result.returncode != 0:
        return []
    citations: dict[str, set[str]] = {}
    for line in result.stdout.splitlines():
        path, separator, content = line.partition("\0")
        if not separator or not path:
            continue
        if set(path.split("/")) & {
            "tests",
            "test",
            "__tests__",
            "fixtures",
            "evals",
            "examples",
            "templates",
        }:
            continue
        for match in _DECISION_ID_RE.finditer(content):
            citations.setdefault(match.group(0), set()).add(path)
    return [
        {"id": citation_id, "file_count": len(files), "files": sorted(files)[:20]}
        for citation_id, files in sorted(citations.items())
    ]


def preflight(repo: Path, skill_root: Path | None = None) -> dict[str, Any]:
    root = _resolve_existing(repo, "repository path")
    if not root.is_dir():
        raise ContractError("preflight repository must be a directory")
    result = _git(root, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        raise ContractError("preflight requires a Git repository root")
    try:
        git_root = Path(result.stdout.strip()).resolve(strict=True)
    except OSError as exc:
        raise ContractError("Git returned an invalid repository root") from exc
    if git_root != root:
        raise ContractError(f"preflight path is not the exact Git root: {git_root}")
    # Host-file and fixed-path problems are reported structurally, never as a crash: the
    # dashboard (which starts from this preflight) must be able to SHOW the invalid state.
    host_errors: list[dict[str, str]] = []
    for relative in (
        "repodocs",
        CONFIG_PATH,
        MANIFEST_PATH,
        "PROJECT_CONTEXT.md",
        "CLAUDE.md",
        "AGENTS.md",
        ".jcodemunch.jsonc",
        ".agents/skills/project-context",
        ".claude",
        ".claude/skills/project-context",
        ".claude/skills/project-context/SKILL.md",
        ".codex",
        ".codex/config.toml",
    ):
        try:
            safe_path(root, relative)
        except ContractError as exc:
            host_errors.append(
                {"path": relative, "error": str(exc).replace(str(root), "<repo>")}
            )
    repodocs = root / "repodocs"
    if os.path.lexists(repodocs) and not _is_symlink_or_junction(repodocs):
        if not repodocs.is_dir():
            host_errors.append(
                {"path": "repodocs", "error": "repodocs must be a directory"}
            )
        else:
            for directory, names, files in os.walk(repodocs, followlinks=False):
                offender = next(
                    (
                        name
                        for name in [*names, *files]
                        if _is_symlink_or_junction(Path(directory) / name)
                    ),
                    None,
                )
                if offender is not None:
                    host_errors.append(
                        {
                            "path": (Path(directory) / offender)
                            .relative_to(root)
                            .as_posix(),
                            "error": "symlinks are not allowed anywhere under repodocs",
                        }
                    )
                    break
    for host, relative in HOST_FILES.items():
        path = root / relative
        if any(entry["path"] == relative for entry in host_errors):
            continue  # already reported by the fixed-path check; one row per path
        if os.path.lexists(path):
            try:
                extract_host_block(
                    _decode_text(_read_regular(path, relative), relative), host
                )
            except ContractError as exc:
                host_errors.append(
                    {"path": relative, "error": str(exc).replace(str(root), "<repo>")}
                )
    legacy = []
    for relative in (
        "project-context.config.yaml",
        "repodocs/project-context.config.yaml",
        "repodocs/audit/inventory.yaml",
        "docs/project-context.config.yaml",
        ".codex/skills/project-context",
    ):
        try:
            if _lexists_in_safe_parent(root, relative):
                legacy.append(relative)
        except ContractError as exc:
            host_errors.append(
                {"path": relative, "error": str(exc).replace(str(root), "<repo>")}
            )
    # v0.1 root agents files: check the exact directory listing, not lexists, so a
    # case-insensitive filesystem never mistakes v0.2's AGENTS.md for legacy agents.md.
    root_entries = set(os.listdir(root))
    legacy.extend(
        name
        for name in ("agents.md", "agents.project-context.md")
        if name in root_entries
    )
    findings_dir = root / "repodocs/audit/findings"
    try:
        safe_path(root, "repodocs/audit/findings", must_exist=True)
        safe_findings_dir = findings_dir.is_dir()
    except ContractError:
        safe_findings_dir = False
    if safe_findings_dir:
        legacy.extend(
            sorted(
                path.relative_to(root).as_posix()
                for path in findings_dir.glob("*.yaml")
            )
        )
    revision_result = _git(root, "rev-parse", "--verify", "HEAD")
    revision = (
        revision_result.stdout.strip() if revision_result.returncode == 0 else None
    )
    if revision is not None and not re.fullmatch(
        r"[0-9a-f]{40}|[0-9a-f]{64}", revision
    ):
        raise ContractError("Git returned an invalid revision")
    context_state, context_error = _context_state(root)
    if host_errors and context_state == "valid":
        context_state = "invalid"
        context_error = f"{host_errors[0]['path']}: {host_errors[0]['error']}"
    evidence = _source_evidence(root)
    worktree_clean = _source_worktree_clean(root)
    exclusions: list[str] = []
    config_exists = False
    try:
        config_path = safe_path(root, CONFIG_PATH, must_exist=True)
        config_exists = config_path.is_file()
        if config_exists:
            config = validate_config(
                strict_json_loads(
                    _decode_text(_read_regular(config_path, CONFIG_PATH), CONFIG_PATH),
                    CONFIG_PATH,
                )
            )
            exclusions = list(config["audit"]["exclude"])
    except ContractError:
        pass  # an absent or unsafe config is already reported through context_state
    instruction_map, instructions_truncated = _agent_instruction_map(root)
    for entry in instruction_map:
        entry["in_excluded_scope"] = _covered_by(entry["path"], exclusions)
    effective_skill = _resolve_existing(
        skill_root or Path(__file__).resolve().parents[1], "skill root"
    )
    return {
        "status": "safe",
        "root": str(root),
        "git_root": str(git_root),
        "revision": revision,
        "worktree_clean": worktree_clean,
        "context_state": context_state,
        "context_error": context_error,
        "host_errors": host_errors,
        "source_state": "codebase" if evidence else "greenfield",
        "source_evidence": evidence,
        "canonical_config": CONFIG_PATH,
        "config_exists": config_exists,
        "legacy_surfaces": legacy,
        "scope_review": _scope_review(root, exclusions),
        "decision_citations": _decision_citations(root),
        "agent_instructions": instruction_map,
        "agent_instructions_truncated": instructions_truncated,
        "skill_root": str(effective_skill),
    }


class _VisibleHTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "template"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "template"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def _visible_governance_sources(text: str) -> list[str]:
    text = re.sub(r"<!--.*?(?:-->|\Z)", "", text, flags=re.DOTALL)
    values: list[str] = []
    fence: tuple[str, int] | None = None
    for line in text.splitlines():
        if fence is not None:
            if re.fullmatch(
                rf" {{0,3}}{re.escape(fence[0])}{{{fence[1]},}}[ \t]*", line
            ):
                fence = None
            continue
        opening = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if opening and (opening.group(1)[0] == "~" or "`" not in opening.group(2)):
            fence = (opening.group(1)[0], len(opening.group(1)))
            continue
        if not line.startswith("- Sources:"):
            continue
        parser = _VisibleHTMLText()
        parser.feed(line[len("- Sources:") :])
        parser.close()
        value = "".join(parser.parts)
        visible: list[str] = []
        index = 0
        while index < len(value):
            visible.append(value[index])
            if (
                value[index] == "]"
                and index + 1 < len(value)
                and value[index + 1] in "(["
            ):
                opener = value[index + 1]
                closer = ")" if opener == "(" else "]"
                depth = 1
                index += 2
                while index < len(value) and depth:
                    if value[index] == "\\" and index + 1 < len(value):
                        index += 2
                        continue
                    if value[index] == opener:
                        depth += 1
                    elif value[index] == closer:
                        depth -= 1
                    index += 1
                continue
            index += 1
        values.append("".join(visible))
    return values


def _validated_project(repo: Path) -> dict[str, Any]:
    root = _resolve_existing(repo, "repository path")
    config_path = safe_path(root, CONFIG_PATH, must_exist=True)
    manifest_path = safe_path(root, MANIFEST_PATH, must_exist=True)
    config_raw = _read_regular(config_path, CONFIG_PATH)
    manifest_raw = _read_regular(manifest_path, MANIFEST_PATH)
    config = validate_config(
        strict_json_loads(_decode_text(config_raw, CONFIG_PATH), CONFIG_PATH)
    )
    manifest = validate_manifest(
        strict_json_loads(_decode_text(manifest_raw, MANIFEST_PATH), MANIFEST_PATH)
    )
    if manifest["schema_version"] == 2:
        return _validated_project_v2(root, config, manifest, config_raw)
    skill_version = (
        (Path(__file__).resolve().parents[1] / "VERSION")
        .read_text(encoding="utf-8")
        .strip()
    )
    warnings: list[str] = []
    if manifest["skill_version"] != skill_version:
        # Contract compatibility is carried by (major, minor); a patch delta is a visible
        # warning, not invalidity - otherwise every docs-only release forces a full re-audit.
        if manifest["skill_version"].split(".")[:2] != skill_version.split(".")[:2]:
            raise ContractError(
                f"context was generated by skill {manifest['skill_version']} but {skill_version} is installed; "
                "the contract versions differ - re-run the audit to regenerate "
                "(see CHANGELOG.md for what changed and references/upgrade.md for the flow)"
            )
        try:
            manifest_newer = int(manifest["skill_version"].split(".")[2]) > int(
                skill_version.split(".")[2]
            )
        except (IndexError, ValueError):
            manifest_newer = False
        if manifest_newer:
            warnings.append(
                f"context was generated by skill {manifest['skill_version']}, newer than the installed "
                f"{skill_version} by a compatible patch - update the skill to match"
            )
        else:
            warnings.append(
                f"context was generated by skill {manifest['skill_version']}; installed {skill_version} is a "
                "compatible patch release - re-audit to refresh when convenient"
            )
    if manifest["config_sha256"] != sha256_text(_decode_text(config_raw, CONFIG_PATH)):
        raise ContractError(
            "manifest config_sha256 does not match normalized config text"
        )
    enabled_hosts = sorted(name for name, enabled in config["hosts"].items() if enabled)
    if sorted(manifest["hosts"]) != enabled_hosts:
        raise ContractError("manifest hosts do not match enabled config hosts")
    manifest_domains = set(manifest["domains"])
    for domain in ("ui", "data"):
        state = config["domains"][domain]
        if state == "enabled" and domain not in manifest_domains:
            raise ContractError(f"manifest omits enabled {domain} domain")
        if state == "disabled" and domain in manifest_domains:
            raise ContractError(f"manifest includes disabled {domain} domain")
    artifacts_by_id: dict[str, dict[str, Any]] = {}
    artifact_bytes: dict[str, bytes] = {}
    host_artifacts: dict[str, str] = {}
    markdown: dict[str, str] = {}
    for artifact in manifest["artifacts"]:
        path = safe_path(root, artifact["path"], must_exist=True)
        raw = _read_regular(path, artifact["path"])
        if artifact["kind"] == "managed_block":
            host = next(
                (
                    name
                    for name, filename in HOST_FILES.items()
                    if filename == artifact["path"]
                ),
                None,
            )
            if host is None or host not in manifest["hosts"]:
                raise ContractError(
                    f"unexpected managed host artifact: {artifact['path']}"
                )
            block = extract_host_block(_decode_text(raw, artifact["path"]), host)
            if block is None:
                raise ContractError(f"managed block is missing from {artifact['path']}")
            if block.replace("\r\n", "\n").replace("\r", "\n") != _host_block(host):
                raise ContractError(
                    f"managed block content drifted in {artifact['path']}"
                )
            actual_hash = sha256_text(block)
            host_artifacts[host] = artifact["path"]
        else:
            decoded = _decode_text(raw, artifact["path"])
            actual_hash = sha256_text(decoded)
            if artifact["kind"] == "owned_file" and artifact["path"].endswith(".md"):
                markdown[artifact["path"]] = decoded
        if actual_hash != artifact["sha256"]:
            hash_subject = (
                "sha256 of the managed block text between the markers, not the whole file"
                if artifact["kind"] == "managed_block"
                else "sha256 of the normalized file text"
            )
            raise ContractError(
                f"artifact hash mismatch: {artifact['path']} (expected {hash_subject})"
            )
        artifacts_by_id[artifact["id"]] = artifact
        artifact_bytes[artifact["id"]] = raw
    if set(host_artifacts) != set(manifest["hosts"]):
        raise ContractError("manifest is missing an enabled host managed block")
    inventory_artifact = artifacts_by_id.get("audit_inventory")
    if (
        inventory_artifact is None
        or inventory_artifact["path"] != "repodocs/audit/inventory.json"
        or inventory_artifact["kind"] != "owned_file"
    ):
        raise ContractError(
            "manifest audit_inventory id must map to repodocs/audit/inventory.json"
        )
    inventory = validate_inventory(
        strict_json_loads(
            _decode_text(artifact_bytes["audit_inventory"], inventory_artifact["path"]),
            inventory_artifact["path"],
        )
    )
    latest_run = inventory["runs"][-1]
    generated_paths = {
        CONFIG_PATH,
        MANIFEST_PATH,
        *(artifact["path"] for artifact in manifest["artifacts"]),
    }
    actual_source_state = (
        "codebase" if _source_evidence(root, generated_paths) else "greenfield"
    )
    if latest_run["source_state"] != actual_source_state:
        raise ContractError(
            "latest inventory source_state does not match the repository"
        )
    if latest_run["verification"]["blind"] != "passed":
        raise ContractError("latest audit run must pass independent blind verification")
    common_artifacts = {
        "decisions": "repodocs/decisions.md",
        "legacy_warning": "repodocs/LegacyWarning.md",
        "migration_backlog": "repodocs/migration-backlog.md",
        "project_map": PROJECT_MAP_PATH,
        "drift_report": "repodocs/audit/drift-report.md",
    }
    topic_artifacts = {
        "architecture": "repodocs/architecture.md",
        "techstack": "repodocs/techstack.md",
        "security": "repodocs/security.md",
        "testing": "repodocs/testing.md",
        "edge_cases": "repodocs/edge-cases.md",
    }
    for artifact_id, artifact_path in common_artifacts.items():
        artifact = artifacts_by_id.get(artifact_id)
        if (
            artifact is None
            or artifact["path"] != artifact_path
            or artifact["kind"] != "owned_file"
        ):
            raise ContractError(
                f"manifest is missing required artifact: {artifact_path}"
            )
    for artifact_id, artifact_path in topic_artifacts.items():
        artifact = artifacts_by_id.get(artifact_id)
        if config["document_layout"] == "full":
            if (
                artifact is None
                or artifact["path"] != artifact_path
                or artifact["kind"] != "owned_file"
            ):
                raise ContractError(f"full layout requires {artifact_path}")
        elif artifact is not None:
            raise ContractError(f"compact layout must embed and omit {artifact_path}")
    approved_exclusions = set(config["audit"]["exclude"])
    recorded_exclusions = set(latest_run["scope"]["excluded"])
    implicit_exclusions = {
        "repodocs",
        ".agents/skills/project-context",
        ".claude/skills/project-context",
    }
    if approved_exclusions - recorded_exclusions:
        raise ContractError("latest inventory omits configured audit exclusions")
    if recorded_exclusions - approved_exclusions - implicit_exclusions:
        raise ContractError("latest inventory contains unapproved audit exclusions")
    conditional_artifacts = {
        "ui": ("ui_kit", "repodocs/ui-kit.md"),
        "data": ("data_model", "repodocs/data-model.md"),
    }
    for domain, (artifact_id, artifact_path) in conditional_artifacts.items():
        resolved = latest_run["domains"][domain]
        if resolved == "unknown":
            raise ContractError(f"latest inventory leaves {domain} domain unresolved")
        enabled = resolved == "enabled"
        if (domain in manifest_domains) != enabled:
            raise ContractError(
                f"manifest {domain} domain does not match latest inventory"
            )
        configured = config["domains"][domain]
        if configured != "auto" and (configured == "enabled") != enabled:
            raise ContractError(
                f"configured {domain} domain does not match latest inventory"
            )
        artifact = artifacts_by_id.get(artifact_id)
        if config["document_layout"] == "full" and enabled:
            if (
                artifact is None
                or artifact["path"] != artifact_path
                or artifact["kind"] != "owned_file"
            ):
                raise ContractError(
                    f"full layout requires {artifact_path} for enabled {domain}"
                )
        elif artifact is not None:
            raise ContractError(
                f"{artifact_path} must be omitted for this layout/domain state"
            )
    if config["document_layout"] == "compact":
        context = markdown["PROJECT_CONTEXT.md"]
        topics = ["stack", "architecture", "security", "testing", "edge-cases"]
        topics.extend(domain for domain in ("ui", "data") if domain in manifest_domains)
        for topic in topics:
            if f"[[context#{topic}]]" not in context:
                raise ContractError(
                    f"compact context is missing canonical topic link: {topic}"
                )
    project_map = validate_project_map(
        strict_json_loads(
            _decode_text(artifact_bytes["project_map"], PROJECT_MAP_PATH),
            PROJECT_MAP_PATH,
        )
    )
    if project_map["run_id"] != latest_run["id"]:
        raise ContractError("project map run_id does not match latest audit run")
    latest_scope = latest_run["scope"]

    def path_is_in_scope(path: str, scope: dict[str, list[str]]) -> bool:
        return (
            _covered_by(path, scope["included"])
            and not _covered_by(path, scope["excluded"])
            and not _covered_by(path, scope["unscanned"])
        )

    for node in project_map["nodes"]:
        for item in node["evidence"]:
            if node["status"] == "planned":
                if item["path"] != "repodocs/decisions.md":
                    raise ContractError(
                        f"planned project-map node needs ADR evidence: {node['id']}"
                    )
            elif not path_is_in_scope(item["path"], latest_scope):
                raise ContractError(
                    f"project-map node evidence is outside latest completed scope: {node['id']}"
                )
    nodes_by_id = {node["id"]: node for node in project_map["nodes"]}
    for edge in project_map["edges"]:
        planned_edge = any(
            nodes_by_id[node_id]["status"] == "planned"
            for node_id in (edge["from"], edge["to"])
        )
        for item in edge["evidence"]:
            if planned_edge and item["path"] != "repodocs/decisions.md":
                raise ContractError(
                    f"planned project-map edge needs ADR evidence: {edge['from']} -> {edge['to']}"
                )
            if not planned_edge and not path_is_in_scope(item["path"], latest_scope):
                raise ContractError(
                    f"project-map edge evidence is outside latest completed scope: {edge['from']} -> {edge['to']}"
                )
    findings_by_auditor: dict[str, dict[str, Any]] = {}
    for auditor in latest_run["coverage"]["completed"]:
        artifact_id = f"finding_{auditor}"
        artifact_path = f"repodocs/audit/findings/{auditor}.json"
        artifact = artifacts_by_id.get(artifact_id)
        if (
            artifact is None
            or artifact["path"] != artifact_path
            or artifact["kind"] != "owned_file"
        ):
            raise ContractError(
                f"manifest is missing completed auditor findings: {auditor}"
            )
        findings = validate_findings(
            strict_json_loads(
                _decode_text(artifact_bytes[artifact_id], artifact_path), artifact_path
            )
        )
        if findings["auditor"] != auditor:
            raise ContractError(f"findings auditor does not match artifact: {auditor}")
        if findings["run_id"] != latest_run["id"]:
            raise ContractError(
                f"findings run_id does not match latest audit run: {auditor}"
            )
        for key in ("included", "excluded", "unscanned"):
            if set(findings["scope"][key]) != set(latest_scope[key]):
                raise ContractError(
                    f"{auditor} findings scope.{key} contradicts inventory scope, even with no findings"
                )
        for finding in findings["findings"]:
            if finding["status"] not in {"new", "persisting"}:
                continue
            if finding["kind"] in {"scope-inconsistency", "agent-directed-text"}:
                # scope_review-driven findings point inside a confirmed exclusion by design:
                # the exclusion is exactly what they report on.
                continue
            paths = [finding["identity"]["path"]]
            paths.extend(item["path"] for item in finding["evidence"])
            paths.extend(
                item["path"] for item in finding["verification"]["counterevidence"]
            )
            for path in paths:
                if not path_is_in_scope(path, latest_scope) or not path_is_in_scope(
                    path, findings["scope"]
                ):
                    raise ContractError(
                        f"active finding is outside completed audit scope: {finding['id']} "
                        f"(identity path {path} is not covered by scope.included minus excluded/unscanned; "
                        "scope entries are literal paths, not globs)"
                    )
        findings_by_auditor[auditor] = findings
    governance_paths = {
        "repodocs/decisions.md",
        "repodocs/LegacyWarning.md",
        "repodocs/migration-backlog.md",
        "repodocs/audit/drift-report.md",
    }
    governance_sources = [
        source
        for path in governance_paths
        for source in _visible_governance_sources(markdown[path])
    ]
    for document in findings_by_auditor.values():
        for finding in document["findings"]:
            if finding["status"] in {"new", "persisting"} and not any(
                re.search(
                    rf"(?<![A-Za-z0-9-]){re.escape(finding['id'])}(?![A-Za-z0-9-])",
                    line,
                )
                for line in governance_sources
            ):
                raise ContractError(
                    f"active finding lacks a disposition reference: {finding['id']}"
                )
    wikilinks = 0
    wikilink_entries: list[dict[str, str]] = []
    artifact_ids_by_path = {
        artifact["path"]: artifact_id
        for artifact_id, artifact in artifacts_by_id.items()
    }
    for relative, text in markdown.items():
        scan_text = strip_code_spans(text)
        tokens = WIKILINK_TOKEN_RE.findall(scan_text)
        if scan_text.count("[[") != len(tokens) or scan_text.count("]]") != len(tokens):
            raise ContractError(f"{relative} has malformed wikilink markers")
        links = WIKILINK_RE.findall(scan_text)
        if len(links) != len(tokens):
            raise ContractError(f"{relative} has malformed wikilinks")
        wikilinks += len(links)
        source_id = artifact_ids_by_path[relative]
        wikilink_entries.extend(
            {"source": source_id, "target": target, "fragment": fragment}
            for target, fragment in links
        )
        unknown_links = sorted({target for target, _ in links} - artifacts_by_id.keys())
        if unknown_links:
            raise ContractError(
                f"{relative} has unknown wikilinks: {', '.join(unknown_links)}"
            )
        for target, fragment in links:
            if fragment:
                target_path = artifacts_by_id[target]["path"]
                target_text = markdown.get(target_path, "")
                if f'<a id="{fragment}"></a>' not in target_text:
                    raise ContractError(
                        f"{relative} has unresolved wikilink anchor: {target}#{fragment}"
                    )
    # The dashboard machine-reads the "## ADR-NNN:" / "## MB-NNN:" heading shape; an anchor
    # without its heading renders that decision or backlog item invisible in the views.
    for governance_path, prefix in (
        ("repodocs/decisions.md", "ADR"),
        ("repodocs/migration-backlog.md", "MB"),
    ):
        # Code spans stay quotable (SKILL.md's documented escape), and the heading regex
        # accepts exactly what _markdown_sections accepts, so the tripwire never fires on
        # content the dashboard actually renders.
        governance_text = strip_code_spans(markdown.get(governance_path, ""))
        headings = set(
            re.findall(
                rf"^##\s+({prefix}-[0-9]{{3,}})\s*(?::|$)",
                governance_text,
                flags=re.MULTILINE,
            )
        )
        for anchor in re.findall(
            rf'<a id="({prefix}-[0-9]{{3,}})"></a>', governance_text
        ):
            if anchor not in headings:
                raise ContractError(
                    f"{governance_path} anchor {anchor} has no matching '## {anchor}: ...' heading "
                    "(the dashboard reads that literal heading shape, in any language)"
                )
    # repodocs/ is the manifest-owned generated surface; a file living there that no
    # manifest entry owns is drift (stale leftover or hand-added document). A warning,
    # not an error: the file may be legitimate user material pending adoption.
    repodocs_root = root / "repodocs"
    if repodocs_root.is_dir() and not _is_symlink_or_junction(repodocs_root):
        for directory, dirnames, filenames in os.walk(repodocs_root, followlinks=False):
            dirnames[:] = sorted(
                d for d in dirnames if not _is_symlink_or_junction(Path(directory) / d)
            )
            for name in sorted(filenames):
                if name == ".DS_Store":
                    continue
                relative = (Path(directory) / name).relative_to(root).as_posix()
                if relative not in generated_paths:
                    warnings.append(
                        f"unmanaged file in repodocs/: {relative} (not owned by the manifest)"
                    )
    summary = {
        "status": "valid",
        "artifacts": len(manifest["artifacts"]),
        "hosts": manifest["hosts"],
        "domains": manifest["domains"],
        "wikilinks": wikilinks,
        "warnings": warnings,
    }
    return {
        "root": root,
        "config": config,
        "manifest": manifest,
        "inventory": inventory,
        "latest_run": latest_run,
        "artifacts": artifacts_by_id,
        "markdown": markdown,
        "findings": findings_by_auditor,
        "project_map": project_map,
        "wikilinks": wikilink_entries,
        "generated_paths": generated_paths,
        "summary": summary,
    }


def validate_project(repo: Path) -> dict[str, Any]:
    return cast(dict[str, Any], _validated_project(repo)["summary"])


def _validated_project_v2(
    root: Path, config: dict[str, Any], manifest: dict[str, Any], config_raw: bytes
) -> dict[str, Any]:
    version = (
        (Path(__file__).resolve().parents[1] / "VERSION")
        .read_text(encoding="utf-8")
        .strip()
    )
    if manifest["skill_version"].split(".")[:2] != version.split(".")[:2]:
        raise ContractError(
            "manifest contract version differs from installed skill; archive and regenerate"
        )
    if manifest["config_sha256"] != sha256_text(_decode_text(config_raw, CONFIG_PATH)):
        raise ContractError("manifest config_sha256 does not match config")
    artifacts = {item["id"]: item for item in manifest["artifacts"]}
    artifact_bytes: dict[str, bytes] = {}
    markdown: dict[str, str] = {}
    for artifact_id, artifact in artifacts.items():
        path = artifact["path"]
        raw = _read_regular(safe_path(root, path, must_exist=True), path)
        if artifact["kind"] == "managed_block":
            host = next(
                (name for name, filename in HOST_FILES.items() if filename == path),
                None,
            )
            if host not in manifest["hosts"]:
                raise ContractError(f"unexpected managed host artifact: {path}")
            block = extract_host_block(_decode_text(raw, path), host)
            if block is None or block.replace("\r\n", "\n").replace(
                "\r", "\n"
            ) != _host_block(host):
                raise ContractError(f"managed block content drifted in {path}")
            actual_hash = sha256_text(block)
        else:
            decoded = _decode_text(raw, path)
            actual_hash = sha256_text(decoded)
            if path.endswith(".md"):
                markdown[path] = decoded
        if actual_hash != artifact["sha256"]:
            raise ContractError(f"artifact hash mismatch: {path}")
        artifact_bytes[artifact_id] = raw
    inventory_artifact = artifacts.get("audit_inventory")
    if (
        inventory_artifact is None
        or inventory_artifact["path"] != "repodocs/audit/inventory.json"
    ):
        raise ContractError("manifest is missing audit inventory")
    inventory = validate_inventory(
        strict_json_loads(
            _decode_text(artifact_bytes["audit_inventory"], inventory_artifact["path"]),
            inventory_artifact["path"],
        )
    )
    if inventory["schema_version"] != 3:
        raise ContractError("manifest v2 requires inventory v3")
    latest = inventory["runs"][-1]
    runs_by_id = {run["id"]: run for run in inventory["runs"]}
    run_ids = set(runs_by_id)
    generated_paths = {
        CONFIG_PATH,
        MANIFEST_PATH,
        *(artifact["path"] for artifact in artifacts.values()),
    }
    actual_source_state = (
        "codebase" if _source_evidence(root, generated_paths) else "greenfield"
    )
    if actual_source_state != latest["source_state"]:
        raise ContractError("latest inventory source_state does not match repository")
    for run in inventory["runs"]:
        report = f"repodocs/audit/reports/{run['id']}.md"
        if report not in markdown:
            raise ContractError(f"manifest is missing audit report: {report}")
    if latest["source_state"] == "greenfield":
        report = markdown[f"repodocs/audit/reports/{latest['id']}.md"]
        if "## Requirements" not in report or "## Open questions" not in report:
            raise ContractError(
                "greenfield audit report must state requirements and open questions"
            )
    findings_by_auditor: dict[str, dict[str, Any]] = {}
    for auditor, result in latest["results"].items():
        artifact = artifacts.get(f"finding_{auditor}")
        path = f"repodocs/audit/findings/{auditor}.json"
        if (
            artifact is None
            or artifact["path"] != path
            or artifact["sha256"] != result["sha256"]
        ):
            raise ContractError(
                f"manifest or inventory result mismatches {auditor} findings"
            )
        document = validate_findings(
            strict_json_loads(
                _decode_text(artifact_bytes[f"finding_{auditor}"], path), path
            )
        )
        if (
            document["schema_version"] != 3
            or document["auditor"] != auditor
            or document["run_id"] != result["source_run_id"]
            or document["scanned_at"] != result["scanned_at"]
        ):
            raise ContractError(
                f"{auditor} findings provenance does not match inventory result"
            )
        if any(
            set(document["scope"][key]) != set(result["scope"][key])
            for key in ("included", "excluded", "unscanned")
        ):
            raise ContractError(
                f"{auditor} findings scope contradicts inventory even with no findings"
            )
        for finding in document["findings"]:
            if finding["status"] not in {"new", "persisting"} or finding["kind"] in {
                "scope-inconsistency",
                "agent-directed-text",
            }:
                continue
            paths = [
                finding["identity"]["path"],
                *(item["path"] for item in finding["evidence"]),
                *(item["path"] for item in finding["verification"]["counterevidence"]),
            ]
            for source_path in paths:
                if not _covered_by(
                    source_path, result["scope"]["included"]
                ) or _covered_by(
                    source_path,
                    [*result["scope"]["excluded"], *result["scope"]["unscanned"]],
                ):
                    raise ContractError(
                        f"active finding is outside completed audit scope: {finding['id']} ({source_path})"
                    )
        findings_by_auditor[auditor] = document
    if latest["source_state"] == "greenfield" and any(
        document["findings"] for document in findings_by_auditor.values()
    ):
        raise ContractError(
            "greenfield audit must not claim defects in nonexistent code"
        )
    approved_exclusions = set(config["audit"]["exclude"])
    recorded_exclusions = set(latest["scope"]["excluded"])
    implicit_exclusions = {
        "repodocs",
        ".agents/skills/project-context",
        ".claude/skills/project-context",
    }
    if (
        approved_exclusions - recorded_exclusions
        or recorded_exclusions - approved_exclusions - implicit_exclusions
    ):
        raise ContractError("latest inventory audit exclusions disagree with config")
    profile = manifest["profile"]
    context_run = next(
        (run for run in inventory["runs"] if run["id"] == manifest["context_run_id"]),
        None,
    )
    project_map: dict[str, Any] = {"nodes": [], "edges": []}
    wikilinks: list[dict[str, str]] = []
    if profile == "audit":
        allowed = {
            "audit_inventory",
            *(f"finding_{auditor}" for auditor in latest["results"]),
        }
        if any(
            artifact_id not in allowed
            and not artifact["path"].startswith("repodocs/audit/reports/")
            for artifact_id, artifact in artifacts.items()
        ):
            raise ContractError(
                "audit profile contains policy documents or managed host blocks"
            )
    else:
        if context_run is None or context_run["verification"]["blind"] != "passed":
            raise ContractError("active context run lacks passed document verification")
        if context_run["source_state"] == "greenfield":
            raise ContractError("greenfield audit cannot connect context")
        if sorted(manifest["hosts"]) != sorted(
            name for name, enabled in config["hosts"].items() if enabled
        ):
            raise ContractError("manifest hosts do not match enabled config hosts")
        context_domains = set(CORE_DOMAINS) | {
            name for name, state in context_run["domains"].items() if state == "enabled"
        }
        if (
            "unknown" in context_run["domains"].values()
            or set(manifest["domains"]) != context_domains
        ):
            raise ContractError("context domains do not match its verified run")
        required_docs = {
            "context": "PROJECT_CONTEXT.md",
            "decisions": "repodocs/decisions.md",
            "legacy_warning": "repodocs/LegacyWarning.md",
            "migration_backlog": "repodocs/migration-backlog.md",
            "drift_report": "repodocs/audit/drift-report.md",
            "project_map": PROJECT_MAP_PATH,
        }
        if config["document_layout"] == "full":
            required_docs.update(
                {
                    "architecture": "repodocs/architecture.md",
                    "techstack": "repodocs/techstack.md",
                    "security": "repodocs/security.md",
                    "testing": "repodocs/testing.md",
                    "edge_cases": "repodocs/edge-cases.md",
                }
            )
        for domain, (artifact_id, path) in {
            "ui": ("ui_kit", "repodocs/ui-kit.md"),
            "data": ("data_model", "repodocs/data-model.md"),
        }.items():
            enabled = context_run["domains"][domain] == "enabled"
            configured = config["domains"][domain]
            if configured != "auto" and (configured == "enabled") != enabled:
                raise ContractError(
                    f"configured {domain} domain contradicts verified context run"
                )
            if config["document_layout"] == "full" and enabled:
                required_docs[artifact_id] = path
            elif artifact_id in artifacts:
                raise ContractError(
                    f"{path} must be omitted for this layout/domain state"
                )
        if config["document_layout"] == "compact":
            for artifact_id in (
                "architecture",
                "techstack",
                "security",
                "testing",
                "edge_cases",
            ):
                if artifact_id in artifacts:
                    raise ContractError("compact layout must embed core policy topics")
            topics = [
                "stack",
                "architecture",
                "security",
                "testing",
                "edge-cases",
                *(domain for domain in ("ui", "data") if domain in context_domains),
            ]
            for topic in topics:
                if f"[[context#{topic}]]" not in markdown["PROJECT_CONTEXT.md"]:
                    raise ContractError(
                        f"compact context is missing canonical topic link: {topic}"
                    )
        for artifact_id, path in required_docs.items():
            if artifact_id not in artifacts or artifacts[artifact_id]["path"] != path:
                raise ContractError(f"active context is missing {path}")
        for path in ("repodocs/decisions.md", "repodocs/LegacyWarning.md"):
            for section in _markdown_sections(markdown[path]):
                if section["deferred"] and (
                    not section["defer_reason"] or not section["review_when"]
                ):
                    raise ContractError(
                        f"{path} {section['id']} deferred decision needs Reason and Review when"
                    )
        headings = {
            item["id"]: item["title"]
            for path, prefix in (
                ("repodocs/decisions.md", "ADR"),
                ("repodocs/migration-backlog.md", "MB"),
            )
            for item in _markdown_sections(markdown[path], prefix)
        }
        reserved = {
            item["id"]: item["title_sha256"] for item in manifest["reserved_ids"]
        }
        for citation in _decision_citations(root):
            identifier = citation["id"]
            if identifier not in headings or reserved.get(identifier) != sha256_text(
                headings[identifier]
            ):
                raise ContractError(
                    f"source-cited {identifier} is missing or repointed; preserve its archived heading and reserved hash"
                )
        for identifier, expected in reserved.items():
            if identifier in headings and sha256_text(headings[identifier]) != expected:
                raise ContractError(f"reserved {identifier} heading was repointed")
        for host in manifest["hosts"]:
            if not any(
                item["path"] == HOST_FILES[host] and item["kind"] == "managed_block"
                for item in artifacts.values()
            ):
                raise ContractError(f"active context is missing {host} host block")
        project_map = validate_project_map(
            strict_json_loads(
                _decode_text(artifact_bytes["project_map"], PROJECT_MAP_PATH),
                PROJECT_MAP_PATH,
            )
        )
        if project_map["run_id"] != context_run["id"]:
            raise ContractError("project map is not bound to active context run")
        context_scope = context_run["scope"]
        accepted_adrs = {
            section["id"]
            for section in _markdown_sections(markdown["repodocs/decisions.md"], "ADR")
            if "Status: accepted" in section["summary"]
        }

        def check_map_evidence(item: dict[str, Any], label: str, planned: bool) -> None:
            if planned:
                if item["path"] != "repodocs/decisions.md" or not any(
                    re.search(
                        rf"(?<![A-Za-z0-9-]){re.escape(adr)}(?![A-Za-z0-9-])",
                        item["detail"],
                    )
                    for adr in accepted_adrs
                ):
                    raise ContractError(
                        f"planned project-map {label} needs accepted ADR evidence"
                    )
            elif not _covered_by(
                item["path"], context_scope["included"]
            ) or _covered_by(
                item["path"], [*context_scope["excluded"], *context_scope["unscanned"]]
            ):
                raise ContractError(
                    f"project-map {label} evidence is outside verified context scope"
                )

        for node in project_map["nodes"]:
            for item in node["evidence"]:
                check_map_evidence(
                    item, f"node {node['id']}", node["status"] == "planned"
                )
        nodes_by_id = {node["id"]: node for node in project_map["nodes"]}
        for edge in project_map["edges"]:
            planned = any(
                nodes_by_id[node_id]["status"] == "planned"
                for node_id in (edge["from"], edge["to"])
            )
            for item in edge["evidence"]:
                check_map_evidence(
                    item, f"edge {edge['from']} -> {edge['to']}", planned
                )
        for artifact in artifacts.values():
            for section in artifact.get("sections", []):
                if section["source_run_id"] not in run_ids:
                    raise ContractError(
                        f"section provenance has unknown run: {section['source_run_id']}"
                    )
                source_run = runs_by_id[section["source_run_id"]]
                source_hashes = {
                    source["path"]: source["sha256"]
                    for source in source_run["source_tree"]
                }
                for source in section["sources"]:
                    path = source["path"]
                    if (
                        source_hashes.get(path) != source["sha256"]
                        or not _covered_by(path, source_run["scope"]["included"])
                        or _covered_by(
                            path,
                            [
                                *source_run["scope"]["excluded"],
                                *source_run["scope"]["unscanned"],
                            ],
                        )
                    ):
                        raise ContractError(
                            f"section source differs from its audit run: {artifact['path']} ({path})"
                        )
        ids_by_path = {
            artifact["path"]: artifact_id for artifact_id, artifact in artifacts.items()
        }
        governance_sources = [
            source
            for path in (
                "repodocs/decisions.md",
                "repodocs/LegacyWarning.md",
                "repodocs/migration-backlog.md",
                "repodocs/audit/drift-report.md",
            )
            for source in _visible_governance_sources(markdown[path])
        ]
        for document in findings_by_auditor.values():
            for finding in document["findings"]:
                if finding["status"] in {"new", "persisting"} and not any(
                    re.search(
                        rf"(?<![A-Za-z0-9-]){re.escape(finding['id'])}(?![A-Za-z0-9-])",
                        line,
                    )
                    for line in governance_sources
                ):
                    raise ContractError(
                        f"active finding lacks a disposition reference: {finding['id']}"
                    )
        for path, document in markdown.items():
            if path.startswith("repodocs/audit/reports/"):
                continue
            scan_text = strip_code_spans(document)
            tokens = WIKILINK_TOKEN_RE.findall(scan_text)
            if (
                scan_text.count("[[") != len(tokens)
                or scan_text.count("]]") != len(tokens)
                or len(WIKILINK_RE.findall(scan_text)) != len(tokens)
            ):
                raise ContractError(f"{path} has malformed wikilinks")
            for target, fragment in WIKILINK_RE.findall(scan_text):
                if target not in artifacts:
                    raise ContractError(f"{path} has unknown wikilink: {target}")
                if fragment and f'<a id="{fragment}"></a>' not in markdown.get(
                    artifacts[target]["path"], ""
                ):
                    raise ContractError(
                        f"{path} has unresolved wikilink: {target}#{fragment}"
                    )
                wikilinks.append(
                    {
                        "source": ids_by_path[path],
                        "target": target,
                        "fragment": fragment,
                    }
                )
        for path, prefix in (
            ("repodocs/decisions.md", "ADR"),
            ("repodocs/migration-backlog.md", "MB"),
        ):
            text = strip_code_spans(markdown[path])
            headings = set(
                re.findall(
                    rf"^##\s+({prefix}-[0-9]{{3,}})\s*(?::|$)", text, flags=re.MULTILINE
                )
            )
            for anchor in re.findall(rf'<a id="({prefix}-[0-9]{{3,}})"></a>', text):
                if anchor not in headings:
                    raise ContractError(
                        f"{path} anchor {anchor} has no matching heading"
                    )
    warnings: list[str] = []
    repodocs_root = root / "repodocs"
    if repodocs_root.is_dir() and not _is_symlink_or_junction(repodocs_root):
        for directory, dirnames, filenames in os.walk(repodocs_root, followlinks=False):
            dirnames[:] = [
                name
                for name in dirnames
                if not _is_symlink_or_junction(Path(directory) / name)
            ]
            for name in filenames:
                relative = (Path(directory) / name).relative_to(root).as_posix()
                if name != ".DS_Store" and relative not in generated_paths:
                    warnings.append(
                        f"unmanaged file in repodocs/: {relative} (not owned by the manifest)"
                    )
    summary = {
        "status": "valid",
        "profile": profile,
        "artifacts": len(artifacts),
        "hosts": manifest["hosts"],
        "domains": manifest["domains"],
        "wikilinks": len(wikilinks),
        "audit_outcome": latest["outcome"],
        "context_run_id": manifest["context_run_id"],
        "warnings": sorted(warnings),
    }
    return {
        "root": root,
        "config": config,
        "manifest": manifest,
        "inventory": inventory,
        "latest_run": latest,
        "context_run": context_run,
        "artifacts": artifacts,
        "markdown": markdown,
        "findings": findings_by_auditor,
        "project_map": project_map,
        "wikilinks": wikilinks,
        "generated_paths": generated_paths,
        "summary": summary,
    }


def _markdown_sections(text: str, prefix: str | None = None) -> list[dict[str, str]]:
    """Extract stable headings as plain text; this is not a Markdown renderer."""
    sections: list[dict[str, str]] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.fullmatch(r"##\s+(.+)", line.strip())
        if not match:
            continue
        title = match.group(1).strip()
        identifier = title.split(":", 1)[0].strip()
        if prefix and not re.fullmatch(rf"{re.escape(prefix)}-[0-9]{{3,}}", identifier):
            continue
        full = []
        for candidate in lines[index + 1 :]:
            if candidate.startswith("## "):
                break
            full.append(candidate.strip())
        fields = {}
        for candidate in full:
            match_field = re.fullmatch(
                r"- (Reason|Review when|Review on):\s*(.*)", candidate
            )
            if match_field:
                fields[match_field.group(1)] = match_field.group(2).strip()
        review_on = fields.get("Review on", "")
        if review_on:
            try:
                due = date.fromisoformat(review_on)
            except ValueError as exc:
                raise ContractError(
                    f"{identifier} Review on must be an ISO date YYYY-MM-DD"
                ) from exc
            if due.isoformat() != review_on:
                raise ContractError(
                    f"{identifier} Review on must be an ISO date YYYY-MM-DD"
                )
        else:
            due = None
        body: list[str] = []
        truncated = False
        for candidate in lines[index + 1 :]:
            if candidate.startswith("## "):
                break
            stripped = candidate.strip()
            # HTML anchors sit above the NEXT entry's heading in the canonical layout;
            # they are navigation markup, not body text, so they neither render nor
            # count toward the cap (a false "truncated" on every 8-line ADR otherwise).
            if (
                not stripped
                or stripped.startswith("<!--")
                or re.fullmatch(r'<a id="[^"]*"></a>', stripped)
            ):
                continue
            # The dashboard shows at most 8 body lines per entry; a ninth line means
            # the source holds more, and silence about that would misrepresent the
            # entry (e.g. an ADR whose Consequences start on line nine).
            if len(body) == 8:
                truncated = True
                break
            body.append(stripped)
        if truncated:
            body.append(
                "… entry truncated here - read the full text in the source document"
            )
        meta = ""
        for line in body:
            found = re.search(
                r"Priority:\s*([^\s·]+)\s*·\s*Effort:\s*([^\s·]+)\s*·\s*Status:\s*(\S+)",
                line,
            )
            if found:
                meta = f"{found.group(1)} · effort {found.group(2)} · {found.group(3)}"
                break
        sections.append(
            {
                "id": identifier,
                "title": title,
                "summary": "\n".join(body),
                "lines": body,
                "meta": meta,
                "defer_reason": fields.get("Reason", ""),
                "review_when": fields.get("Review when", ""),
                "review_on": review_on,
                "review_due": due is not None
                and due <= datetime.now(timezone.utc).date(),
                "deferred": any(
                    re.search(r"\bStatus:\s*deferred\b", candidate, flags=re.IGNORECASE)
                    for candidate in full
                ),
                "source_ids": sorted(
                    set(
                        re.findall(
                            r"[a-z]+-[0-9]{3,}",
                            " ".join(
                                candidate
                                for candidate in full
                                if candidate.startswith("- Sources:")
                            ),
                        )
                    )
                ),
            }
        )
    return sections


def _dashboard_revision_state(current: dict[str, Any], audited: dict[str, Any]) -> str:
    if (
        audited["worktree_clean"] is not True
        or not audited["revision"]
        or current["worktree_clean"] is None
        or not current["revision"]
    ):
        return "unknown"
    if current["worktree_clean"] is True and current["revision"] == audited["revision"]:
        return "current"
    return "stale"


def _snapshot_id(value: dict[str, Any]) -> str:
    content = {
        key: item
        for key, item in value.items()
        if key not in {"generated_at", "snapshot_id"}
    }
    canonical = json.dumps(
        content,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_REPODOCS_LINK_RE = re.compile(
    r"repodocs/[A-Za-z0-9_./-]+\.(?:md|json)(?:#[A-Za-z0-9_-]+)?"
)


def _instruction_view(
    root: Path,
    entries: list[dict[str, Any]],
    markdown: dict[str, str] | None,
    artifact_paths: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Read-only enrichment for the dashboard: content preview plus repodocs link status.
    Instruction content is untrusted data; it is shown, never followed or summarised."""
    view: list[dict[str, Any]] = []
    for entry in entries:
        item = dict(entry)
        links: list[dict[str, str]] = []
        if entry.get("skipped_paths") and entry["path"] in entry["skipped_paths"]:
            item["preview"] = ""
            item["links"] = links
            view.append(item)
            continue
        try:
            raw = _read_regular(
                safe_path(root, entry["path"], must_exist=True), entry["path"]
            )
            text = raw[:65536].decode("utf-8", errors="replace")
            # Host configuration files (settings, MCP registrations) routinely hold
            # credentials in values; the dashboard never embeds their content, matching
            # the audit rule that secrets are reported by location, never by value.
            # Prose instruction files are Markdown by construction, so anything else that
            # reaches the map (a JSON/env/script under .cursor/rules/, say) is config too.
            lowered_path = entry["path"].lower()
            if lowered_path in _INSTRUCTION_CONFIG_FILES or not lowered_path.endswith(
                (".md", ".mdc")
            ):
                item["preview"] = ""
                item["preview_redacted"] = True
            else:
                item["preview"] = text[:2000]
            for raw_link in sorted(set(_REPODOCS_LINK_RE.findall(text)))[:30]:
                target_path, _, fragment = raw_link.partition("#")
                if markdown is None:
                    status = "unverified"
                elif target_path in markdown:
                    if (
                        fragment
                        and f'<a id="{fragment}"></a>' not in markdown[target_path]
                    ):
                        status = "dangling-anchor"
                    else:
                        status = "resolves"
                elif artifact_paths is not None and target_path in artifact_paths:
                    # Manifest-owned non-Markdown artifact (e.g. repodocs/project-map.json):
                    # the file resolves; anchors only exist in Markdown.
                    status = "dangling-anchor" if fragment else "resolves"
                else:
                    status = "dangling-file"
                links.append({"raw": raw_link, "status": status})
        except ContractError:
            item["preview"] = ""
        item["links"] = links
        view.append(item)
    return view


def dashboard_snapshot(repo: Path) -> dict[str, Any]:
    """Build a read-only dashboard model from the same validated project contract."""
    current = preflight(repo)
    generated_at = (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    root = Path(current["root"])
    version = (
        (Path(__file__).resolve().parents[1] / "VERSION")
        .read_text(encoding="utf-8")
        .strip()
    )
    instruction_limits = [
        f"{path}: instruction inventory skipped unsafe path"
        for entry in current["agent_instructions"]
        for path in entry.get("skipped_paths", [])
    ]
    model: dict[str, Any] = {
        "schema_version": 2,
        "generated_at": generated_at,
        "project": {
            "name": root.name,
            "revision": current["revision"],
            "short_revision": current["revision"][:8] if current["revision"] else None,
            "worktree_clean": current["worktree_clean"],
            "revision_state": "unknown",
        },
        "context": {
            "state": current["context_state"],
            "error": current["context_error"],
            "skill_version": version,
            "profile": None,
            "verified_run_id": None,
            "revision_state": "unknown",
            "language": "en",
            "layout": None,
            "hosts": [],
            "domains": [],
        },
        "audit": {"latest": None, "history": [], "report": ""},
        "findings": [],
        "finding_summary": {
            "total": 0,
            "active": 0,
            "critical_high": 0,
            "active_set_sha256": sha256_bytes(b"[]"),
        },
        "project_map": {"nodes": [], "edges": []},
        "context_map": {"nodes": [], "edges": []},
        "documents": {"decisions": [], "debt": [], "backlog": [], "drift": []},
        "integrity": {
            "status": current["context_state"],
            "artifacts": 0,
            "wikilinks": 0,
            "checks": [],
            "limitations": [
                "Structural validation does not prove factual completeness.",
                *(
                    f"{entry['path']}: {entry['error']}"
                    for entry in current["host_errors"]
                ),
                *instruction_limits,
            ],
        },
    }
    if current["context_state"] != "valid":
        model["agent_instructions"] = _instruction_view(
            root, current["agent_instructions"], None
        )
        model["agent_instructions_truncated"] = current["agent_instructions_truncated"]
        model["snapshot_id"] = _snapshot_id(model)
        return model

    try:
        project = _validated_project(root)
    except ContractError as exc:
        model["context"]["state"] = "invalid"
        model["context"]["error"] = str(exc).replace(str(root), "<repo>")
        model["integrity"]["status"] = "invalid"
        model["agent_instructions"] = _instruction_view(
            root, current["agent_instructions"], None
        )
        model["agent_instructions_truncated"] = current["agent_instructions_truncated"]
        model["snapshot_id"] = _snapshot_id(model)
        return model

    config = project["config"]
    manifest = project["manifest"]
    latest = project["latest_run"]
    model["drift"] = _drift_project(project)
    flattened: list[dict[str, Any]] = []
    for auditor, document in project["findings"].items():
        for finding in document["findings"]:
            verification = finding["verification"]
            flattened.append(
                {
                    **finding,
                    "auditor": auditor,
                    "source_severity": finding["severity"],
                    "effective_severity": verification.get("resulting_severity")
                    or finding["severity"],
                    "identity_sha256": sha256_bytes(
                        json.dumps(
                            finding["identity"],
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode("utf-8")
                    ),
                }
            )
    flattened.sort(
        key=lambda item: (
            item["status"] not in {"new", "persisting"},
            -SEVERITY_RANK[item["effective_severity"]],
            item["auditor"],
            item["id"],
        )
    )
    active = [item for item in flattened if item["status"] in {"new", "persisting"}]
    revision_state = (
        model["drift"]["status"]
        if manifest["schema_version"] == 2
        else _dashboard_revision_state(current, latest)
    )
    model["project"]["revision_state"] = revision_state
    profile = manifest.get("profile", "context")
    context_run = project.get("context_run", latest)
    model["context"]["state"] = "audit-only" if profile == "audit" else "valid"
    model["context"].update(
        {
            "profile": profile,
            "verified_run_id": context_run["id"] if context_run else None,
            "revision_state": model["drift"].get(
                "context_status",
                _dashboard_revision_state(current, context_run)
                if context_run
                else "unknown",
            ),
            "language": config["language"],
            "layout": config["document_layout"],
            "hosts": manifest["hosts"],
            "domains": manifest["domains"],
            "warnings": project["summary"].get("warnings", []),
        }
    )
    model["audit"] = {
        "latest": latest,
        "history": list(reversed(project["inventory"]["runs"])),
        "report": next(
            (
                body
                for path, body in project["markdown"].items()
                if path == f"repodocs/audit/reports/{latest['id']}.md"
            ),
            "",
        ),
    }
    model["findings"] = flattened
    model["finding_summary"] = {
        "total": len(flattened),
        "active": len(active),
        "critical_high": sum(
            item["effective_severity"] in {"critical", "high"} for item in active
        ),
        "active_set_sha256": sha256_bytes(
            json.dumps(
                sorted(
                    (
                        item["auditor"],
                        item["id"],
                        item["status"],
                        item["identity_sha256"],
                    )
                    for item in active
                ),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ),
    }
    model["project_map"] = {
        "nodes": project["project_map"]["nodes"],
        "edges": project["project_map"]["edges"],
    }
    artifact_paths = {
        artifact_id: artifact["path"]
        for artifact_id, artifact in project["artifacts"].items()
    }
    artifact_source_runs = {"audit_inventory": latest["id"]}
    artifact_source_runs.update(
        {
            artifact_id: run["id"]
            for artifact_id, artifact in project["artifacts"].items()
            for run in project["inventory"]["runs"]
            if artifact["path"] == f"repodocs/audit/reports/{run['id']}.md"
        }
    )
    if manifest["schema_version"] == 2:
        artifact_source_runs.update(
            {
                f"finding_{auditor}": result["source_run_id"]
                for auditor, result in latest["results"].items()
            }
        )
    model["context_map"] = {
        "nodes": [
            {
                "id": artifact_id,
                "label": artifact["path"],
                "group": "Context artifacts",
                "kind": artifact["kind"].replace("_", " "),
                "status": "current",
                "preview": project["markdown"]
                .get(artifact["path"], "")
                .encode("utf-8")[:65536]
                .decode("utf-8", errors="replace")
                if artifact["path"] in project["markdown"]
                else "",
                "preview_truncated": len(
                    project["markdown"].get(artifact["path"], "").encode("utf-8")
                )
                > 65536,
                "source_run_id": artifact_source_runs.get(
                    artifact_id, context_run["id"] if context_run else latest["id"]
                ),
                "sections": artifact.get("sections", []),
                "evidence": [
                    {
                        "path": artifact["path"],
                        "detail": "Manifest-owned validated artifact.",
                    }
                ],
            }
            for artifact_id, artifact in sorted(project["artifacts"].items())
        ],
        "edges": [
            {
                "from": link["source"],
                "to": link["target"],
                "label": f"links to #{link['fragment']}"
                if link["fragment"]
                else "links to",
                "evidence": [
                    {
                        "path": artifact_paths[link["source"]],
                        "detail": "Validated wikilink reference.",
                    }
                ],
            }
            for link in project["wikilinks"]
        ],
    }
    markdown = project["markdown"]
    model["documents"] = {
        "decisions": _markdown_sections(
            markdown.get("repodocs/decisions.md", ""), "ADR"
        ),
        "debt": _markdown_sections(markdown.get("repodocs/LegacyWarning.md", "")),
        "backlog": _markdown_sections(
            markdown.get("repodocs/migration-backlog.md", ""), "MB"
        ),
        "drift": _markdown_sections(markdown.get("repodocs/audit/drift-report.md", "")),
    }
    deferred = [
        entry
        for entry in [*model["documents"]["decisions"], *model["documents"]["debt"]]
        if entry["deferred"]
    ]
    for finding in flattened:
        finding["deferred"] = [
            {
                "id": entry["id"],
                "reason": entry["defer_reason"],
                "review_when": entry["review_when"],
                "review_on": entry["review_on"],
                "review_due": entry["review_due"],
            }
            for entry in deferred
            if finding["id"] in entry["source_ids"]
        ]
    limitations = [
        "Structural validation does not prove factual completeness.",
        *instruction_limits,
    ]
    if revision_state == "unknown":
        limitations.append(
            "Source freshness is unknown because the audit or current worktree is not cleanly anchored."
        )
    elif revision_state == "stale":
        limitations.append(
            "Repository HEAD or worktree changed after the latest audit."
        )
    model["integrity"] = {
        "status": "valid",
        "artifacts": project["summary"]["artifacts"],
        "wikilinks": project["summary"]["wikilinks"],
        "checks": [
            {"name": "Manifest and hashes", "status": "passed"},
            {"name": "Audit run binding", "status": "passed"},
            {
                "name": "Document verification",
                "status": context_run["verification"]["blind"]
                if context_run
                else "not-run",
            },
            {"name": "HEAD matches audit", "status": revision_state},
        ],
        "limitations": limitations,
    }
    model["agent_instructions"] = _instruction_view(
        root, current["agent_instructions"], markdown, project["generated_paths"]
    )
    model["agent_instructions_truncated"] = current["agent_instructions_truncated"]
    model["snapshot_id"] = _snapshot_id(model)
    return model


def validate_remediation(repo: Path, value: Any) -> dict[str, Any]:
    binding = _mapping(value, "remediation binding")
    selection_kind = _enum(
        binding.get("selection_kind", "remediation"),
        {"remediation", "regression"},
        "remediation.selection_kind",
    )
    repository = _mapping(binding.get("repository"), "remediation.repository")
    snapshot = dashboard_snapshot(repo)
    if snapshot["integrity"]["status"] != "valid":
        raise ContractError("remediation requires valid manifest-owned artifacts")
    if snapshot["project"]["revision_state"] != "current":
        raise ContractError(
            "remediation snapshot is stale or source freshness is unknown"
        )
    if (
        repository.get("snapshot_id") != snapshot["snapshot_id"]
        or repository.get("audit_run_id") != snapshot["audit"]["latest"]["id"]
    ):
        raise ContractError(
            "remediation snapshot or audit run changed; refresh the dashboard"
        )
    summary = snapshot["finding_summary"]
    if (
        binding.get("expected_active_count") != summary["active"]
        or binding.get("expected_active_sha256") != summary["active_set_sha256"]
    ):
        raise ContractError(
            "the complete active finding set changed; regenerate the selection"
        )
    selected = _list(binding.get("selected_findings"), "remediation.selected_findings")
    if not selected or len(selected) > (1 if selection_kind == "regression" else 250):
        raise ContractError(
            "select exactly one closed finding"
            if selection_kind == "regression"
            else "select between 1 and 250 active findings per wave"
        )
    eligible_statuses = (
        {"resolved", "refuted"}
        if selection_kind == "regression"
        else {"new", "persisting"}
    )
    eligible = {
        (item["auditor"], item["id"]): item
        for item in snapshot["findings"]
        if item["status"] in eligible_statuses
    }
    seen: set[tuple[str, str]] = set()
    for raw in selected:
        item = _mapping(raw, "remediation.selected_findings item")
        key = (
            _text(item.get("auditor"), "remediation.auditor"),
            _text(item.get("id"), "remediation.id"),
        )
        canonical = eligible.get(key)
        if (
            key in seen
            or canonical is None
            or item.get("identity_sha256") != canonical["identity_sha256"]
            or item.get("status") != canonical["status"]
        ):
            raise ContractError(
                f"selected finding changed or was duplicated: {key[0]}/{key[1]}"
            )
        seen.add(key)
    return {
        "status": "valid",
        "selected": len(selected),
        "selection_kind": selection_kind,
        "active": summary["active"],
        "audit_run_id": repository["audit_run_id"],
    }


def _drift_project(project: dict[str, Any]) -> dict[str, Any]:
    if project["manifest"]["schema_version"] != 2:
        return {"status": "unknown", "reason": "legacy manifest has no source hashes"}
    root = project["root"]
    latest = project["latest_run"]
    known: dict[str, set[str]] = {}
    baseline = {source["path"]: source["sha256"] for source in latest["source_tree"]}
    for auditor, result in latest["results"].items():
        for source in result["sources"]:
            path = source["path"]
            known.setdefault(path, set()).add(auditor)
    changed: list[str] = []
    missing: list[str] = []
    for path, expected in sorted(baseline.items()):
        try:
            raw = _read_regular(safe_path(root, path, must_exist=True), path)
        except ContractError:
            missing.append(path)
            continue
        if sha256_bytes(raw) != expected:
            changed.append(path)
    listed = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if listed.returncode != 0:
        return {"status": "unknown", "reason": "Git source inventory unavailable"}
    generated = project["generated_paths"]
    source_paths = {
        path
        for path in listed.stdout.split("\0")
        if path
        and path not in generated
        and not path.startswith(
            (".agents/skills/project-context/", ".claude/skills/project-context/")
        )
    }
    added = sorted(source_paths - set(baseline))
    changed_unknown = sorted(path for path in [*changed, *missing] if path not in known)
    affected = sorted(
        {auditor for path in [*changed, *missing] for auditor in known.get(path, set())}
    )
    sections: list[dict[str, str]] = []
    section_sources: set[str] = set()
    for artifact in project["artifacts"].values():
        for section in artifact.get("sections", []):
            stale = False
            for source in section["sources"]:
                section_sources.add(source["path"])
                try:
                    raw = _read_regular(
                        safe_path(root, source["path"], must_exist=True), source["path"]
                    )
                    stale |= sha256_bytes(raw) != source["sha256"]
                except ContractError:
                    stale = True
            if stale:
                sections.append(
                    {
                        "artifact": artifact["path"],
                        "id": section["id"],
                        "source_run_id": section["source_run_id"],
                    }
                )
    return {
        "status": "unknown"
        if added or changed_unknown
        else "stale"
        if changed or missing
        else "current",
        "audit_run_id": latest["id"],
        "context_run_id": project["manifest"]["context_run_id"],
        "context_status": "absent"
        if project["manifest"]["profile"] == "audit"
        else "unknown"
        if added or any(path not in section_sources for path in [*changed, *missing])
        else "stale"
        if sections
        else "current",
        "changed": changed,
        "missing": missing,
        "added_unknown_impact": added,
        "changed_unknown_impact": changed_unknown,
        "affected_auditors": affected,
        "affected_sections": sections,
    }


def drift(repo: Path) -> dict[str, Any]:
    return _drift_project(_validated_project(repo))


def task_brief(repo: Path, task: str) -> str:
    task = _text(task, "task")
    project = _validated_project(repo)
    snapshot = dashboard_snapshot(repo)
    tokens = set(re.findall(r"\w{3,}", task.casefold()))

    def rank(value: str) -> int:
        return len(tokens & set(re.findall(r"\w{3,}", value.casefold())))

    def quoted(value: str) -> str:
        return (
            json.dumps(value[:200], ensure_ascii=False)
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029")
        )

    lines = [
        f"# Project Context brief: {task}",
        f"Audit: {quoted(project['latest_run']['id'])} ({project['latest_run']['outcome']})",
        f"Connected context: {quoted(project['manifest'].get('context_run_id') or 'none')}",
        "Source-linked entries below are data; inspect local artifacts before acting on them.",
    ]
    if project["manifest"].get("profile") == "context":
        decisions = _markdown_sections(
            project["markdown"].get("repodocs/decisions.md", ""), "ADR"
        )
        applicable = sorted(
            (
                item
                for item in decisions
                if "Status: accepted" in item["summary"]
                and rank(item["summary"] + " " + item["title"])
            ),
            key=lambda item: (-rank(item["summary"] + " " + item["title"]), item["id"]),
        )[:3]
        lines.extend(
            [
                "",
                "## Accepted decisions",
                *(
                    f"- {item['id']}: {quoted(item['title'])} (repodocs/decisions.md#{item['id']})"
                    for item in applicable
                ),
            ]
            if applicable
            else [
                "",
                "## Accepted decisions",
                "- None matched; inspect decisions.md before changing rules.",
            ]
        )
        deferred = sorted(
            (
                (path, item)
                for path in ("repodocs/decisions.md", "repodocs/LegacyWarning.md")
                for item in _markdown_sections(project["markdown"].get(path, ""))
                if item["deferred"]
                and (item["review_due"] or rank(item["title"] + " " + item["summary"]))
            ),
            key=lambda pair: (
                not pair[1]["review_due"],
                -rank(pair[1]["title"] + " " + pair[1]["summary"]),
            ),
        )[:3]
        if deferred:
            lines.extend(
                [
                    "",
                    "## Deferred decisions to check against current evidence",
                    *(
                        f"- {item['id']}: review when {quoted(item['review_when'])}"
                        f"{' (date due)' if item['review_due'] else ''} ({path}#{item['id']})"
                        for path, item in deferred
                    ),
                ]
            )
        facts = [
            (path, item)
            for path, body in project["markdown"].items()
            if path not in {"repodocs/decisions.md", "PROJECT_CONTEXT.md"}
            and not path.startswith("repodocs/audit/")
            for item in _markdown_sections(body)
            if rank(item["title"] + " " + item["summary"])
        ]
        facts.sort(
            key=lambda pair: (
                -rank(pair[1]["title"] + " " + pair[1]["summary"]),
                pair[0],
            )
        )
        lines.extend(
            [
                "",
                "## Verified context pointers",
                *(
                    f"- {quoted(item['title'])} ({quoted(path)}; context run {quoted(project['manifest']['context_run_id'])})"
                    for path, item in facts[:4]
                ),
            ]
        )
        testing_path = (
            "repodocs/testing.md"
            if "repodocs/testing.md" in project["markdown"]
            else "PROJECT_CONTEXT.md"
        )
        testing = project["markdown"].get(testing_path, "")
        checks = [
            line.strip()
            for line in testing.splitlines()
            if rank(line)
            and re.search(r"test|check|verify|провер|тест", line, flags=re.IGNORECASE)
        ][:3]
        lines.extend(
            [
                "",
                "## Checks",
                *(f"- {quoted(line)} ({testing_path})" for line in checks),
            ]
        )
    else:
        lines.extend(
            [
                "",
                "## Policy",
                "- No connected context. Audit findings are pending evidence, not accepted rules.",
            ]
        )
    matched = [
        item
        for item in snapshot["findings"]
        if item["status"] in {"new", "persisting"}
        and rank(
            item["title"]
            + " "
            + item["identity"]["path"]
            + " "
            + item["identity"]["assertion"]
        )
    ]
    lines.extend(
        [
            "",
            "## Active findings",
            *(
                f"- {item['id']}: {quoted(item['title'])} (repodocs/audit/findings/{item['auditor']}.json)"
                for item in matched[:5]
            ),
        ]
    )
    paths = sorted({item["identity"]["path"] for item in matched[:5]})
    lines.extend(["", "## Source files", *(f"- {quoted(path)}" for path in paths)])
    unknown = [
        *project["latest_run"]["scope"]["unscanned"],
        *snapshot.get("drift", {}).get("added_unknown_impact", []),
        *snapshot.get("drift", {}).get("changed_unknown_impact", []),
    ]
    lines.extend(
        ["", "## Unknown or unscanned", *(f"- {quoted(path)}" for path in unknown[:5])]
        if unknown
        else [
            "",
            "## Unknown or unscanned",
            "- No recorded gaps for this task; factual completeness is not implied.",
        ]
    )
    return "\n".join(lines)[:8192] + "\n"


def preview_context(
    repo: Path, candidate_dir: Path, classification: Any
) -> dict[str, Any]:
    project = _validated_project(repo)
    latest = project["latest_run"]
    if latest["source_state"] != "codebase":
        raise ContractError("greenfield audit cannot preview connected context")
    candidate = _resolve_existing(candidate_dir, "candidate directory")
    if not candidate.is_dir():
        raise ContractError("candidate must be a directory")
    entries = _mapping(classification, "classification")
    _exact_keys(entries, {"changes"}, "classification")
    classified = {}
    for raw in _list(entries["changes"], "classification.changes"):
        item = _mapping(raw, "classification change")
        _exact_keys(
            item, {"path", "kind", "summary", "adr_id"}, "classification change"
        )
        path = validate_relative_path(item["path"])
        if path in classified:
            raise ContractError(f"duplicate classification: {path}")
        _enum(item["kind"], {"fact", "rule", "gap"}, "classification.kind")
        _text(item["summary"], "classification.summary")
        if item["adr_id"] is not None:
            _text(item["adr_id"], "classification.adr_id")
        classified[path] = item
    allowed = {
        "PROJECT_CONTEXT.md",
        "repodocs/decisions.md",
        "repodocs/LegacyWarning.md",
        "repodocs/migration-backlog.md",
        "repodocs/audit/drift-report.md",
        PROJECT_MAP_PATH,
    }
    if project["config"]["document_layout"] == "full":
        allowed.update(
            f"repodocs/{name}.md"
            for name in (
                "architecture",
                "techstack",
                "security",
                "testing",
                "edge-cases",
            )
        )
        allowed.update(
            path
            for domain, path in (
                ("ui", "repodocs/ui-kit.md"),
                ("data", "repodocs/data-model.md"),
            )
            if latest["domains"][domain] == "enabled"
        )
    removable = {
        artifact["path"]
        for artifact in project["artifacts"].values()
        if artifact["path"] in {"repodocs/ui-kit.md", "repodocs/data-model.md"}
        and artifact["path"] not in allowed
    }
    allowed.update(removable)
    changes = []
    proposed_docs = {}
    for directory, names, files in os.walk(candidate, followlinks=False):
        for name in [*names, *files]:
            path = Path(directory) / name
            if _is_symlink_or_junction(path):
                raise ContractError("candidate contains a symlink")
        for name in files:
            relative = (Path(directory) / name).relative_to(candidate).as_posix()
            if relative not in allowed:
                raise ContractError(
                    f"candidate contains non-policy or unowned file: {relative}"
                )
            proposed = _decode_text(
                _read_regular(
                    safe_path(candidate, relative, must_exist=True), relative
                ),
                relative,
            )
            if relative == PROJECT_MAP_PATH:
                document = validate_project_map(strict_json_loads(proposed, relative))
                if document["run_id"] != latest["id"]:
                    raise ContractError(
                        "candidate project map must refer to the latest audit run"
                    )
            proposed_docs[relative] = proposed
            original = project["markdown"].get(relative)
            if original is None:
                original = (
                    _decode_text(
                        _read_regular(
                            safe_path(project["root"], relative, must_exist=True),
                            relative,
                        ),
                        relative,
                    )
                    if relative == PROJECT_MAP_PATH
                    and project["manifest"]["profile"] == "context"
                    else ""
                )
            if proposed == original:
                continue
            item = classified.get(relative)
            if item is None:
                raise ContractError(
                    f"changed document lacks classification: {relative}"
                )
            changes.append(
                {
                    "path": relative,
                    "kind": item["kind"],
                    "summary": item["summary"],
                    "adr_id": item["adr_id"],
                    "disposition": "pending",
                    "diff": "".join(
                        difflib.unified_diff(
                            original.splitlines(keepends=True),
                            proposed.splitlines(keepends=True),
                            fromfile=f"a/{relative}",
                            tofile=f"b/{relative}",
                        )
                    ),
                }
            )
    for relative in sorted(set(classified) - set(proposed_docs)):
        if relative not in removable:
            raise ContractError(
                f"classification names an absent required or unowned document: {relative}"
            )
        original = project["markdown"][relative]
        item = classified[relative]
        changes.append(
            {
                "path": relative,
                "kind": item["kind"],
                "summary": item["summary"],
                "adr_id": item["adr_id"],
                "disposition": "pending",
                "diff": "".join(
                    difflib.unified_diff(
                        original.splitlines(keepends=True),
                        [],
                        fromfile=f"a/{relative}",
                        tofile=f"b/{relative}",
                    )
                ),
            }
        )
    if set(classified) != {item["path"] for item in changes}:
        raise ContractError("classification must list exactly changed policy documents")
    accepted = {
        section["id"]
        for section in _markdown_sections(
            proposed_docs.get(
                "repodocs/decisions.md",
                project["markdown"].get("repodocs/decisions.md", ""),
            ),
            "ADR",
        )
        if "Status: accepted" in section["summary"]
    }
    for change in changes:
        if change["kind"] == "fact" or (
            change["kind"] == "rule" and change["adr_id"] in accepted
        ):
            change["disposition"] = "ready"
    return {
        "status": "pending"
        if any(item["disposition"] == "pending" for item in changes)
        else "ready",
        "changes": changes,
    }


def archive_legacy(repo: Path, destination: Path) -> dict[str, Any]:
    root = _resolve_existing(repo, "repository path")
    manifest_raw = _read_regular(
        safe_path(root, MANIFEST_PATH, must_exist=True), MANIFEST_PATH
    )
    manifest = validate_manifest(
        strict_json_loads(_decode_text(manifest_raw, MANIFEST_PATH), MANIFEST_PATH)
    )
    if manifest["schema_version"] != 1:
        raise ContractError("archive-legacy expects a v0.5.x manifest v1")
    config_raw = _read_regular(
        safe_path(root, CONFIG_PATH, must_exist=True), CONFIG_PATH
    )
    validate_config(
        strict_json_loads(_decode_text(config_raw, CONFIG_PATH), CONFIG_PATH)
    )
    if sha256_text(_decode_text(config_raw, CONFIG_PATH)) != manifest["config_sha256"]:
        raise ContractError("legacy config hash does not match manifest")
    parent = _resolve_existing(destination.parent, "archive parent")
    if parent == root or root in parent.parents:
        raise ContractError("archive destination must be outside the repository")
    target = parent / destination.name
    if os.path.lexists(target):
        raise ContractError("archive destination already exists")
    files = {CONFIG_PATH: config_raw, MANIFEST_PATH: manifest_raw}
    for artifact in manifest["artifacts"]:
        path = artifact["path"]
        raw = _read_regular(safe_path(root, path, must_exist=True), path)
        if artifact["kind"] == "managed_block":
            host = next(
                name for name, filename in HOST_FILES.items() if filename == path
            )
            block = extract_host_block(_decode_text(raw, path), host)
            actual = sha256_text(block or "")
        else:
            actual = sha256_text(_decode_text(raw, path))
            files[path] = raw
        if actual != artifact["sha256"]:
            raise ContractError(f"legacy artifact hash mismatch: {path}")
    headings = {
        item["id"]: item["title"]
        for path, prefix in (
            ("repodocs/decisions.md", "ADR"),
            ("repodocs/migration-backlog.md", "MB"),
        )
        if path in files
        for item in _markdown_sections(_decode_text(files[path], path), prefix)
    }
    cited = {item["id"] for item in _decision_citations(root)}
    reserved = [
        {"id": identifier, "title_sha256": sha256_text(headings[identifier])}
        for identifier in sorted(cited & headings.keys())
    ]
    if cited - headings.keys():
        raise ContractError(
            f"source-cited IDs are absent from legacy decisions/backlog: {', '.join(sorted(cited - headings.keys()))}"
        )
    temporary = Path(tempfile.mkdtemp(prefix=".project-context-archive-", dir=parent))
    try:
        for path, raw in files.items():
            output = temporary / path
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(raw)
        (temporary / "archive.json").write_text(
            json.dumps(
                {
                    "legacy_manifest_sha256": sha256_bytes(manifest_raw),
                    "files": sorted(files),
                    "reserved_ids": reserved,
                    "history": "archived-v0.5; new inventory v3 starts a new series",
                },
                indent=2,
            )
            + "\n"
        )
        if os.path.lexists(target):
            raise ContractError("archive destination appeared during copy")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {
        "status": "archived",
        "path": str(target),
        "files": len(files),
        "reserved_ids": reserved,
    }


def _dashboard_json(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return (
        encoded.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_dashboard_html(
    repo: Path, route: str = "/", nonce: str | None = None
) -> bytes:
    if not re.fullmatch(r"/(?:[a-zA-Z0-9_-]+/)?", route):
        raise ContractError("dashboard route is invalid")
    nonce = nonce or secrets.token_urlsafe(18)
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", nonce):
        raise ContractError("dashboard CSP nonce is invalid")
    template_path = Path(__file__).resolve().parents[1] / "assets/dashboard.html"
    template = _decode_text(
        _read_regular(template_path, "assets/dashboard.html"), "assets/dashboard.html"
    )
    replacements = {
        "__CSP_NONCE__": nonce,
        "__DASHBOARD_ROUTE__": route,
        # Insert repository-derived data last so marker-like text inside it stays inert.
        "__SNAPSHOT__": _dashboard_json(dashboard_snapshot(repo)),
    }
    for marker, replacement in replacements.items():
        if marker not in template:
            raise ContractError(f"dashboard template is missing {marker}")
        template = template.replace(marker, replacement)
    return template.encode("utf-8")


def _dashboard_handler_class(root: Path, route: str) -> type[BaseHTTPRequestHandler]:
    """The dashboard's HTTP boundary, extracted so tests can exercise it over real sockets."""

    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "ProjectContext/1"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _headers(
            self, status: int, length: int, nonce: str, content_type: str
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Security-Policy",
                "; ".join(
                    (
                        "default-src 'none'",
                        f"script-src 'nonce-{nonce}'",
                        f"style-src 'nonce-{nonce}'",
                        "img-src data:",
                        "connect-src 'none'",
                        "form-action 'self'",
                        "base-uri 'none'",
                        "frame-ancestors 'none'",
                    )
                ),
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
            )
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.end_headers()

        def _page(self, *, head: bool = False) -> None:
            dashboard_server = cast(ThreadingHTTPServer, self.server)
            expected_host = f"127.0.0.1:{dashboard_server.server_port}"
            if self.headers.get("Host") not in {
                expected_host,
                f"localhost:{dashboard_server.server_port}",
            }:
                self._plain(400, b"invalid host\n", head=head)
                return
            try:
                parsed = urlsplit(self.path)
            except ValueError:
                self._plain(400, b"invalid request target\n", head=head)
                return
            if (
                not self.path.startswith("/")
                or self.path.startswith("//")
                or parsed.scheme
                or parsed.netloc
                or parsed.path != route
                or parsed.query
                or parsed.fragment
            ):
                self._plain(404, b"not found\n", head=head)
                return
            nonce = secrets.token_urlsafe(18)
            try:
                body = render_dashboard_html(root, route, nonce)
            except ContractError:
                self._plain(500, b"dashboard validation failed\n", head=head)
                return
            self._headers(200, len(body), nonce, "text/html; charset=utf-8")
            if not head:
                self.wfile.write(body)

        def _plain(self, status: int, body: bytes, *, head: bool = False) -> None:
            nonce = secrets.token_urlsafe(18)
            self._headers(status, len(body), nonce, "text/plain; charset=utf-8")
            if not head:
                self.wfile.write(body)

        def _method_not_allowed(self) -> None:
            body = b"method not allowed\n"
            self.send_response(405)
            self.send_header("Allow", "GET, HEAD")
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        do_GET = _page

        def do_HEAD(self) -> None:
            self._page(head=True)

        do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = do_CONNECT = (
            _method_not_allowed
        )

    return DashboardHandler


def serve_dashboard(repo: Path, *, open_browser: bool = True) -> None:
    root = _resolve_existing(repo, "repository path")
    # Validate the fixed root before opening a socket; requests can never choose another path.
    preflight(root)
    token = secrets.token_hex(16)
    route = f"/{token}/"
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), _dashboard_handler_class(root, route)
    )
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_port}{route}"
    print(f"Project Context dashboard: {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def self_check(skill_root: Path) -> dict[str, Any]:
    root = _resolve_existing(skill_root, "skill root")
    required = {
        "SKILL.md",
        "README.md",
        "CHANGELOG.md",
        "RELEASING.md",
        "LICENSE",
        "VERSION",
        "agents/openai.yaml",
        "assets/dashboard.html",
        "assets/project-context-icon.svg",
        "assets/project-context-logo.svg",
        "scripts/project_context.py",
        "evals/README.md",
        "evals/paired-study.md",
        "evals/cases.json",
        "evals/fixtures/build.sh",
        "evals/scorecard.template.json",
        "examples/README.md",
        "schemas/findings.schema.json",
        "schemas/project-map.schema.json",
        "examples/PROJECT_CONTEXT.md",
        "examples/inventory.json",
        "examples/project-map.json",
        "templates/PROJECT_CONTEXT.md",
        "templates/audit-inventory.json",
        "templates/project-context.config.json",
        "templates/project-context.manifest.json",
        "templates/project-map.json",
        "templates/host/AGENTS.block.md",
        "templates/host/CLAUDE.block.md",
        "templates/host/claude-skill-adapter.md",
        "templates/host/codex-config.fragment.toml",
        "templates/jcodemunch.jsonc",
    }
    required |= {
        f"auditors/{name}.md"
        for name in (
            "_common",
            "architecture",
            "bloat",
            "data",
            "performance",
            "security",
            "stack",
            "testing",
            "ui",
        )
    }
    required |= {
        f"references/{name}.md"
        for name in (
            "decision-matrix",
            "diff-review",
            "findings-schema",
            "greenfield",
            "host-integration",
            "jcodemunch",
            "upgrade",
        )
    }
    required |= {
        f"templates/{name}.md"
        for name in (
            "audit-report",
            "CurrentSprint",
            "LegacyWarning",
            "architecture",
            "data-model",
            "decisions",
            "drift-report",
            "edge-cases",
            "migration-backlog",
            "security",
            "techstack",
            "testing",
            "ui-kit",
        )
    }
    missing = sorted(
        relative for relative in required if not (root / relative).is_file()
    )
    if missing:
        raise ContractError(f"skill payload is missing: {', '.join(missing)}")
    for relative in sorted(required):
        if not _decode_text(_read_regular(root / relative, relative), relative).strip():
            raise ContractError(f"skill payload file is empty: {relative}")
    # The registry is two-way: a payload file that is not registered here would ship
    # silently (installed, never verified). Sweep the payload directories and reject
    # unregistered files, skipping only runtime caches.
    _junk_dirs = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
    unregistered: list[str] = []
    for payload_dir in (
        "agents",
        "assets",
        "auditors",
        "evals",
        "examples",
        "references",
        "schemas",
        "scripts",
        "templates",
    ):
        base = root / payload_dir
        if not base.is_dir():
            continue
        for directory, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if d not in _junk_dirs)
            for name in sorted(filenames):
                if name == ".DS_Store" or name.endswith((".pyc", ".pyo")):
                    continue
                relative = (Path(directory) / name).relative_to(root).as_posix()
                if relative not in required:
                    unregistered.append(relative)
    if unregistered:
        raise ContractError(
            "skill payload contains unregistered files (add them to the self-check registry "
            f"or remove them): {', '.join(sorted(unregistered))}"
        )
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ContractError("VERSION must contain x.y.z")
    load_json(root / "schemas/findings.schema.json")
    load_json(root / "schemas/project-map.schema.json")
    validate_config(load_json(root / "templates/project-context.config.json"))
    validate_inventory(load_json(root / "templates/audit-inventory.json"))
    validate_project_map(load_json(root / "templates/project-map.json"))
    validate_project_map(load_json(root / "examples/project-map.json"))
    manifest = validate_manifest(
        load_json(root / "templates/project-context.manifest.json")
    )
    if manifest["skill_version"] != version:
        raise ContractError("template manifest skill_version does not match VERSION")
    validate_inventory(load_json(root / "examples/inventory.json"))
    scorecard = _mapping(
        load_json(root / "evals/scorecard.template.json"), "evals scorecard template"
    )
    _exact_keys(
        scorecard,
        {"schema_version", "skill_version", "runs"},
        "evals scorecard template",
    )
    for index, raw in enumerate(
        _list(scorecard["runs"], "evals scorecard template runs")
    ):
        run_entry = _mapping(raw, f"evals scorecard template runs[{index}]")
        _exact_keys(
            run_entry,
            {
                "case_id",
                "run_at",
                "passed",
                "expected_met",
                "expected_missed",
                "forbidden_hit",
                "notes",
            },
            f"evals scorecard template runs[{index}]",
        )
    cases = _mapping(load_json(root / "evals/cases.json"), "evals")
    _exact_keys(cases, {"schema_version", "cases"}, "evals")
    if type(cases["schema_version"]) is not int or cases["schema_version"] != 1:
        raise ContractError("evals.schema_version must be 1")
    case_ids: set[str] = set()
    for index, raw in enumerate(_list(cases["cases"], "evals.cases")):
        case = _mapping(raw, f"evals.cases[{index}]")
        _exact_keys(
            case,
            {"id", "request", "setup", "expected", "forbidden"},
            f"evals.cases[{index}]",
        )
        case_id = _text(case["id"], f"evals.cases[{index}].id")
        if case_id in case_ids:
            raise ContractError(f"duplicate eval case id: {case_id}")
        case_ids.add(case_id)
        _text(case["request"], f"evals.cases[{index}].request")
        for field in ("setup", "expected", "forbidden"):
            if not _unique_strings(case[field], f"evals.cases[{index}].{field}"):
                raise ContractError(f"evals.cases[{index}].{field} must not be empty")
    for host in HOST_FILES:
        block = _host_block(host)
        if extract_host_block(block, host) != block:
            raise ContractError(f"invalid {host} host block template")
    return {"status": "valid", "version": version, "files_checked": len(required)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser(
        "self-check",
        help="verify the installed skill payload (registry, templates, schemas, evals)",
    )
    command.add_argument(
        "--skill-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    command = commands.add_parser(
        "preflight",
        help="inspect a repository before an audit: context state, instruction map, citations",
    )
    command.add_argument("--repo", required=True, type=Path)
    command.add_argument(
        "--skill-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    command = commands.add_parser(
        "merge-host",
        help="merge the managed block into a host file; prints the merged text to stdout",
    )
    command.add_argument("--host", required=True, choices=sorted(HOST_FILES))
    command.add_argument("--input", type=Path)
    command.add_argument(
        "--expected-sha256",
        help="optimistic lock: abort when the host file changed after preview",
    )
    command.add_argument("--allow-create", action="store_true")
    command.add_argument(
        "--input-sha256",
        action="store_true",
        help="print the input hash needed for --apply",
    )
    command.add_argument(
        "--apply",
        action="store_true",
        help="atomically apply a reviewed merge to the fixed root host file",
    )
    command.add_argument("--repo", type=Path, help="repository root for --apply")
    command = commands.add_parser(
        "validate-project",
        help="validate the whole generated context of a repository against the manifest",
    )
    command.add_argument("--repo", required=True, type=Path)
    command = commands.add_parser(
        "validate-remediation",
        help="verify a dashboard selection against the current active finding set",
    )
    command.add_argument("--repo", required=True, type=Path)
    command.add_argument("--input", required=True, type=Path)
    command = commands.add_parser(
        "drift", help="read-only source-hash and scope drift report"
    )
    command.add_argument("--repo", required=True, type=Path)
    command = commands.add_parser(
        "brief", help="read-only, source-linked context selection for a task"
    )
    command.add_argument("--repo", required=True, type=Path)
    command.add_argument("--task", required=True)
    command = commands.add_parser(
        "preview-context",
        help="read-only exact diff and rule-decision check for candidate policy documents",
    )
    command.add_argument("--repo", required=True, type=Path)
    command.add_argument("--candidate-dir", required=True, type=Path)
    command.add_argument(
        "--input", required=True, type=Path, help="classification JSON"
    )
    command = commands.add_parser(
        "archive-legacy",
        help="archive validated v0.5 artifacts before v0.6 regeneration",
    )
    command.add_argument("--repo", required=True, type=Path)
    command.add_argument(
        "--out",
        required=True,
        type=Path,
        help="new archive directory outside the repository",
    )
    command = commands.add_parser(
        "validate-config", help="validate one project-context.config.json document"
    )
    command.add_argument("--input", required=True, type=Path)
    command = commands.add_parser(
        "validate-findings",
        help="validate one auditor findings document, optionally against its predecessor",
    )
    command.add_argument("--input", required=True, type=Path)
    command.add_argument("--previous", type=Path)
    command.add_argument(
        "--previous-sha256",
        help="expected sha256:<hex> of the previous file's normalized text, from the last valid manifest",
    )
    command.add_argument("--allow-provisional", action="store_true")
    command = commands.add_parser(
        "validate-inventory",
        help="validate one audit inventory, optionally enforcing append-only history",
    )
    command.add_argument("--input", required=True, type=Path)
    command.add_argument("--previous", type=Path)
    command = commands.add_parser(
        "validate-project-map", help="validate one project-map.json document"
    )
    command.add_argument("--input", required=True, type=Path)
    command = commands.add_parser(
        "validate-manifest", help="validate one project-context.manifest.json document"
    )
    command.add_argument("--input", required=True, type=Path)
    command = commands.add_parser(
        "dashboard", help="serve the read-only local dashboard for a repository"
    )
    command.add_argument("--repo", required=True, type=Path)
    command.add_argument("--no-open", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "self-check":
            dump(self_check(args.skill_root))
        elif args.command == "preflight":
            dump(preflight(args.repo, args.skill_root))
        elif args.command == "merge-host":
            if args.apply:
                if args.repo is None or args.input is not None or args.input_sha256:
                    raise ContractError(
                        "--apply requires --repo and cannot use --input or --input-sha256"
                    )
                dump(
                    {
                        "status": "applied",
                        "sha256": apply_host_file(
                            args.repo,
                            args.host,
                            args.expected_sha256,
                            allow_create=args.allow_create,
                        ),
                    }
                )
            else:
                if args.repo is not None:
                    raise ContractError("--repo requires --apply")
                if args.input and args.input.exists() and not args.input.is_file():
                    raise ContractError("merge-host --input must be a regular file")
                raw = (
                    args.input.read_bytes()
                    if args.input and args.input.exists()
                    else b""
                )
                if not raw and not args.allow_create:
                    # A same-file shell redirect truncates the input before Python reads it;
                    # without this guard that silently erases the user's host file.
                    raise ContractError(
                        "merge-host input is missing or empty; pass --allow-create only for a genuinely new file, "
                        "and never redirect onto the input file in the same command"
                    )
                bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
                text = _decode_text(raw, str(args.input or "host input"))
                if args.input_sha256:
                    print(sha256_bytes(raw))
                else:
                    merged = merge_host_text(text, args.host, args.expected_sha256)
                    sys.stdout.buffer.write(bom + merged.encode("utf-8"))
        elif args.command == "validate-project":
            dump(validate_project(args.repo))
        elif args.command == "validate-remediation":
            dump(validate_remediation(args.repo, load_json(args.input)))
        elif args.command == "drift":
            dump(drift(args.repo))
        elif args.command == "brief":
            print(task_brief(args.repo, args.task), end="")
        elif args.command == "preview-context":
            dump(preview_context(args.repo, args.candidate_dir, load_json(args.input)))
        elif args.command == "archive-legacy":
            dump(archive_legacy(args.repo, args.out))
        elif args.command == "validate-config":
            validate_config(load_json(args.input))
            dump({"status": "valid"})
        elif args.command == "validate-findings":
            if args.previous_sha256 and not args.previous:
                raise ContractError("--previous-sha256 requires --previous")
            if args.previous and not args.previous_sha256:
                raise ContractError(
                    "--previous requires --previous-sha256 from the last valid manifest"
                )
            if args.previous and args.previous_sha256:
                if not HASH_RE.fullmatch(args.previous_sha256):
                    raise ContractError(
                        "--previous-sha256 is not a valid sha256:<hex> value"
                    )
                try:
                    previous_raw = args.previous.read_bytes()
                except OSError as exc:
                    raise ContractError(
                        f"cannot read --previous file {args.previous}: {exc.strerror or exc}"
                    ) from exc
                # The manifest hashes normalized text (BOM stripped, line endings unified),
                # so the guard must too - otherwise a CRLF checkout rejects genuine history.
                if (
                    sha256_text(_decode_text(previous_raw, str(args.previous)))
                    != args.previous_sha256
                ):
                    raise ContractError(
                        "previous findings file does not match the recorded manifest hash (unknown provenance): "
                        "do not inherit its ids or refuted history - start a fresh series and record the "
                        "discontinuity in the drift report"
                    )
            current = validate_findings(
                load_json(args.input),
                load_json(args.previous) if args.previous else None,
                allow_provisional=args.allow_provisional,
            )
            dump({"status": "valid", "findings": len(current["findings"])})
        elif args.command == "validate-inventory":
            inventory = validate_inventory(
                load_json(args.input),
                load_json(args.previous) if args.previous else None,
            )
            dump({"status": "valid", "runs": len(inventory["runs"])})
        elif args.command == "validate-project-map":
            project_map = validate_project_map(load_json(args.input))
            dump(
                {
                    "status": "valid",
                    "nodes": len(project_map["nodes"]),
                    "edges": len(project_map["edges"]),
                }
            )
        elif args.command == "validate-manifest":
            manifest = validate_manifest(load_json(args.input))
            dump({"status": "valid", "artifacts": len(manifest["artifacts"])})
        elif args.command == "dashboard":
            serve_dashboard(args.repo, open_browser=not args.no_open)
        return 0
    except ContractError as exc:
        print(
            json.dumps({"error": str(exc), "code": exc.code}, ensure_ascii=False),
            file=sys.stderr,
        )
        return exc.code
    except Exception as exc:  # noqa: BLE001 - stable CLI error boundary
        print(
            json.dumps(
                {
                    "error": f"unexpected validator failure ({type(exc).__name__})",
                    "code": 70,
                }
            ),
            file=sys.stderr,
        )
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
