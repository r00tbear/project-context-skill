"""Small stdlib validator for project-context generated files."""

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import webbrowser
from datetime import datetime, timezone
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
WIKILINK_RE = re.compile(r"\[\[([a-z][a-z0-9_-]*)(?:#([A-Za-z0-9][A-Za-z0-9_-]*))?(?:\|[^\]\n]+)?\]\]")
WIKILINK_TOKEN_RE = re.compile(r"\[\[([^\[\]\n]+)\]\]")
FENCED_CODE_RE = re.compile(r"^ {0,3}(```|~~~).*?^ {0,3}\1[ \t]*$", re.MULTILINE | re.DOTALL)
INLINE_CODE_RE = re.compile(r"``[^`\n](?:[^`\n]|`(?!`))*``|`[^`\n]*`")


def strip_code_spans(text: str) -> str:
    """Code spans may quote a wikilink without creating one; drop them before link extraction."""
    return INLINE_CODE_RE.sub("", FENCED_CODE_RE.sub("", text))
CORE_DOMAINS = {"architecture", "stack", "security", "testing"}
ALL_DOMAINS = CORE_DOMAINS | {"ui", "data"}
ARTIFACT_KINDS = {"owned_file", "managed_block"}
SEVERITIES = {"low", "medium", "high", "critical"}
SEVERITY_RANK = {name: rank for rank, name in enumerate(("low", "medium", "high", "critical"))}
CORE_AUDITORS = {"stack", "architecture", "bloat", "security", "testing"}
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
    print(json.dumps(value, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True))


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
        raise ContractError(f"{label} must be a{' non-empty' if nonempty else ''} string")
    return value


def _exact_keys(value: dict[str, Any], required: set[str], label: str, optional: set[str] | None = None) -> None:
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


def _unique_strings(value: Any, label: str, choices: set[str] | None = None) -> list[str]:
    items = _list(value, label)
    if not all(isinstance(item, str) for item in items):
        raise ContractError(f"{label} must contain only strings")
    if len(items) != len(set(items)):
        raise ContractError(f"{label} must not contain duplicates")
    if choices is not None:
        unknown = set(items) - choices
        if unknown:
            raise ContractError(f"{label} contains unknown values: {', '.join(sorted(unknown))}")
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
                raise ContractError(f"cannot inspect {rel}: {exc.strerror or exc}") from exc
            # Junctions (IO_REPARSE_TAG_MOUNT_POINT) are the reparse points that behave like
            # directory symlinks; other reparse tags (cloud placeholders, app-exec aliases)
            # are ordinary files and must not be rejected.
            reparse_tag = getattr(lstat_result, "st_reparse_tag", 0)
            if stat.S_ISLNK(lstat_result.st_mode) or reparse_tag == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003):
                raise ContractError(f"symlink is not allowed in generated path: {rel}")
    try:
        candidate.resolve(strict=False).relative_to(root)
    except (OSError, ValueError) as exc:
        raise ContractError(f"path escapes repository root: {rel}") from exc
    if must_exist and not os.path.lexists(candidate):
        raise ContractError(f"required artifact is missing: {rel}")
    return candidate


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
        {"schema_version", "user_level", "language", "document_layout", "domains", "hosts", "audit"},
        "config",
    )
    if type(config["schema_version"]) is not int or config["schema_version"] != 1:
        raise ContractError("config.schema_version must be 1")
    _enum(config["user_level"], {"novice", "specialist", "expert"}, "config.user_level")
    if not isinstance(config["language"], str) or not LANGUAGE_RE.fullmatch(config["language"]):
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
    return any(root == "." or path == root or path.startswith(root + "/") for root in roots)


def _validate_evidence(value: Any, label: str) -> dict[str, Any]:
    evidence = _mapping(value, label)
    _exact_keys(evidence, {"path", "detail"}, label, {"line"})
    validate_relative_path(evidence["path"], f"{label}.path")
    _text(evidence["detail"], f"{label}.detail")
    if "line" in evidence and (type(evidence["line"]) is not int or evidence["line"] < 1):
        raise ContractError(f"{label}.line must be a positive integer")
    return evidence


def validate_findings(value: Any, previous: Any | None = None, *, allow_provisional: bool = False) -> dict[str, Any]:
    document = _mapping(value, "findings document")
    _exact_keys(
        document,
        {"schema_version", "run_id", "auditor", "scanned_at", "scope", "findings"},
        "findings document",
    )
    if type(document["schema_version"]) is not int or document["schema_version"] != 2:
        raise ContractError("findings.schema_version must be 2")
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
            {"id", "kind", "title", "severity", "confidence", "identity", "status", "evidence", "verification"},
            label,
        )
        finding_id = _text(finding["id"], f"{label}.id")
        if not FINDING_ID_RE.fullmatch(finding_id) or not finding_id.startswith(document["auditor"] + "-") or finding_id in ids:
            raise ContractError(f"{label}.id must be unique and prefixed by its auditor")
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
        status = _enum(finding["status"], {"new", "persisting", "resolved", "refuted"}, f"{label}.status")
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
        if "resulting_severity" in verification and verification["resulting_severity"] is not None:
            _enum(verification["resulting_severity"], SEVERITIES, f"{label}.verification.resulting_severity")
        counter = _list(verification["counterevidence"], f"{label}.verification.counterevidence")
        for counter_index, item in enumerate(counter):
            _validate_evidence(item, f"{label}.verification.counterevidence[{counter_index}]")
        if verification_status == "pending":
            if not allow_provisional or severity not in {"high", "critical"} or status not in {"new", "persisting"}:
                raise ContractError(f"{label} pending verification is allowed only for active high/critical candidates")
            if counter or verification.get("resulting_severity") is not None:
                raise ContractError(f"{label} pending verification must not claim a result")
        if verification_status in {"downgraded", "refuted"} and not counter:
            raise ContractError(f"{label} downgraded/refuted verification needs counterevidence")
        if (status == "refuted") != (verification_status == "refuted"):
            raise ContractError(f"{label} finding and verification refuted statuses must match")
        if verification_status == "downgraded":
            resulting_severity = verification.get("resulting_severity")
            if resulting_severity is None or SEVERITY_RANK[resulting_severity] >= SEVERITY_RANK[severity]:
                raise ContractError(f"{label} downgraded verification needs a lower resulting severity")
        elif verification.get("resulting_severity") not in (None, severity):
            raise ContractError(f"{label} resulting_severity must match severity unless downgraded")
        if severity in {"high", "critical"} and verification_status == "not-required":
            raise ContractError(f"{label} high/critical finding needs adversarial verification")
    if previous is not None:
        old = validate_findings(previous)
        if old["auditor"] != document["auditor"]:
            raise ContractError("previous findings use a different auditor")
        current_by_id = {item["id"]: item for item in records}
        for item in old["findings"]:
            if item["id"] not in current_by_id:
                raise ContractError(f"previous finding id was removed: {item['id']}")
            current = current_by_id[item["id"]]
            if (current["kind"], current["identity"]) != (item["kind"], item["identity"]):
                same_anchor = current["kind"] == item["kind"] and all(
                    current["identity"].get(key) == item["identity"].get(key) for key in ("path", "symbol")
                )
                if same_anchor:
                    raise ContractError(
                        f"identity.assertion changed for persisting finding {item['id']}: "
                        "copy identity byte for byte from the previous run and put new wording in title or evidence detail"
                    )
                raise ContractError(f"previous finding id was reused: {item['id']}")
            if item["status"] == "refuted" and current["status"] in {"new", "persisting"}:
                raise ContractError(f"refuted finding cannot return as active under the same id: {item['id']}")
            if item["status"] in {"new", "persisting"} and current["status"] == "resolved":
                path = current["identity"]["path"]
                old_scope = old["scope"]
                scope = document["scope"]
                if any(
                    not _covered_by(path, candidate["included"])
                    or _covered_by(path, [*candidate["excluded"], *candidate["unscanned"]])
                    for candidate in (old_scope, scope)
                ):
                    raise ContractError(
                        f"resolved finding was outside comparable completed scope: {item['id']} (identity path: {path})"
                    )
    return document


def validate_inventory(value: Any, previous: Any | None = None) -> dict[str, Any]:
    inventory = _mapping(value, "inventory")
    _exact_keys(inventory, {"schema_version", "runs"}, "inventory")
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
            not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision)
        ):
            raise ContractError(f"{label}.revision must be a Git object id or null")
        if run["worktree_clean"] is not None and type(run["worktree_clean"]) is not bool:
            raise ContractError(f"{label}.worktree_clean must be a boolean or null")
        _enum(run["source_state"], {"greenfield", "codebase"}, f"{label}.source_state")
        outcome = _enum(run["outcome"], {"complete", "coverage-incomplete", "failed"}, f"{label}.outcome")
        domains = _mapping(run["domains"], f"{label}.domains")
        _exact_keys(domains, {"ui", "data"}, f"{label}.domains")
        for name, state in domains.items():
            _enum(state, {"enabled", "absent", "unknown"}, f"{label}.domains.{name}")
        coverage = _mapping(run["coverage"], f"{label}.coverage")
        _exact_keys(coverage, {"required", "completed", "skipped", "failed"}, f"{label}.coverage")
        required = _unique_strings(coverage["required"], f"{label}.coverage.required", AUDITORS)
        completed = _unique_strings(coverage["completed"], f"{label}.coverage.completed", AUDITORS)
        failed = _unique_strings(coverage["failed"], f"{label}.coverage.failed", AUDITORS)
        skipped = _mapping(coverage["skipped"], f"{label}.coverage.skipped")
        for name, reason in skipped.items():
            _enum(name, AUDITORS, f"{label}.coverage.skipped key")
            _text(reason, f"{label}.coverage.skipped.{name}")
        if (set(completed) & set(failed)) or (set(completed) & set(skipped)) or (set(failed) & set(skipped)):
            raise ContractError(f"{label}.coverage auditor states overlap")
        expected_required = {"greenfield"} if run["source_state"] == "greenfield" else CORE_AUDITORS | {
            name for name, state in domains.items() if state == "enabled"
        }
        if set(required) != expected_required:
            raise ContractError(f"{label}.coverage.required does not match source state and domains")
        if not set(completed) <= set(required) or not set(failed) <= set(required):
            raise ContractError(f"{label}.coverage completed/failed must be required auditors")
        scope = _validate_scope(run["scope"], f"{label}.scope")
        tools = _mapping(run["tools"], f"{label}.tools")
        _exact_keys(tools, {"jcodemunch"}, f"{label}.tools")
        for name, state in tools.items():
            _enum(state, {"used", "unavailable", "skipped", "failed"}, f"{label}.tools.{name}")
        verification = _mapping(run["verification"], f"{label}.verification")
        _exact_keys(verification, {"blind", "issues"}, f"{label}.verification")
        blind = _enum(verification["blind"], {"passed", "failed", "not-run"}, f"{label}.verification.blind")
        issues = verification["issues"]
        if type(issues) is not int or issues < 0:
            raise ContractError(f"{label}.verification.issues must be a non-negative integer")
        if blind == "failed" and issues == 0:
            raise ContractError(f"{label}.verification.failed needs at least one issue")
        if blind != "failed" and issues != 0:
            raise ContractError(f"{label}.verification.issues must be zero unless blind verification failed")
        expected = "failed" if failed or blind == "failed" else (
            "coverage-incomplete"
            if (
                blind != "passed"
                or scope["unscanned"]
                or "unknown" in domains.values()
                or set(completed) != set(required)
            )
            else "complete"
        )
        if outcome != expected:
            raise ContractError(f"{label}.outcome must be {expected}")
    if previous is not None:
        old = validate_inventory(previous)
        old_runs = old["runs"]
        if len(runs) < len(old_runs) or runs[: len(old_runs)] != old_runs:
            raise ContractError("inventory history is not append-only")
    return inventory


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
        _exact_keys(node, {"id", "label", "kind", "status", "evidence"}, label, {"group"})
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
        return path in {"PROJECT_CONTEXT.md", ".jcodemunch.jsonc"} or path.startswith("repodocs/")
    if kind == "managed_block":
        return path in HOST_FILES.values()
    return False


def validate_manifest(value: Any) -> dict[str, Any]:
    manifest = _mapping(value, "manifest")
    _exact_keys(manifest, {"schema_version", "skill_version", "config_sha256", "domains", "hosts", "artifacts"}, "manifest")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise ContractError("manifest.schema_version must be 1")
    version = _text(manifest["skill_version"], "manifest.skill_version")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ContractError("manifest.skill_version must be semantic version x.y.z")
    if not isinstance(manifest["config_sha256"], str) or not HASH_RE.fullmatch(manifest["config_sha256"]):
        raise ContractError("manifest.config_sha256 must be sha256:<64 lowercase hex>")
    domains = _unique_strings(manifest["domains"], "manifest.domains", ALL_DOMAINS)
    if not CORE_DOMAINS <= set(domains):
        raise ContractError("manifest.domains must include architecture, stack, security, and testing")
    _unique_strings(manifest["hosts"], "manifest.hosts", set(HOST_FILES))
    artifacts = _list(manifest["artifacts"], "manifest.artifacts")
    if not artifacts:
        raise ContractError("manifest.artifacts must not be empty")
    ids: set[str] = set()
    paths: set[str] = set()
    for index, raw in enumerate(artifacts):
        label = f"manifest.artifacts[{index}]"
        artifact = _mapping(raw, label)
        _exact_keys(artifact, {"id", "path", "kind", "sha256"}, label)
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
        if not isinstance(artifact["sha256"], str) or not HASH_RE.fullmatch(artifact["sha256"]):
            raise ContractError(f"{label}.sha256 must be sha256:<64 lowercase hex>")
    context = next((item for item in artifacts if item["id"] == "context"), None)
    if context is None or context["path"] != "PROJECT_CONTEXT.md" or context["kind"] != "owned_file":
        raise ContractError("manifest context id must map to the owned PROJECT_CONTEXT.md")
    return manifest


def _host_block(host: str) -> str:
    try:
        filename = "CLAUDE.block.md" if host == "claude" else "AGENTS.block.md"
        raw = (Path(__file__).resolve().parents[1] / "templates/host" / filename).read_text(encoding="utf-8")
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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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
    return block is not None and not (text[: text.index(block)] + text[text.index(block) + len(block) :]).strip()


def _verified_generated_paths(root: Path) -> set[str]:
    """Trust generated ownership only when the complete manifest still validates."""
    try:
        validate_project(root)
        manifest = validate_manifest(load_json(root / MANIFEST_PATH))
    except ContractError:
        return set()
    return {CONFIG_PATH, MANIFEST_PATH, *(artifact["path"] for artifact in manifest["artifacts"])}


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
            if any(rel == prefix or rel.startswith(prefix + "/") for prefix in ignored_prefixes):
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
                host = next((name for name, filename in HOST_FILES.items() if filename == rel), None)
                if host is None or _only_managed_host_content(path, host):
                    continue
            if not rel_dir and name in {".DS_Store", ".gitattributes", ".gitignore", "CODE_OF_CONDUCT.md"}:
                continue
            if not rel_dir and name in {"LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "LICENCE.md", "LICENCE.txt"}:
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
    ignored_prefixes = (".agents/skills/project-context", ".claude/skills/project-context")
    for line in result.stdout.splitlines():
        path = line[3:] if len(line) >= 4 else line
        paths = path.split(" -> ") if " -> " in path else [path]
        for candidate in paths:
            if any(candidate == prefix or candidate.startswith(prefix + "/") for prefix in ignored_prefixes):
                continue
            if candidate in generated:
                host = next((name for name, filename in HOST_FILES.items() if filename == candidate), None)
                if host is not None and _host_user_content_changed(root, candidate, host):
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
    has_repodocs = repodocs.is_dir() and any(repodocs.iterdir())
    has_managed_host = False
    host_problem: str | None = None
    for host, relative in HOST_FILES.items():
        path = root / relative
        if path.is_file():
            try:
                text = _decode_text(_read_regular(path, relative), relative)
                has_managed_host = has_managed_host or extract_host_block(text, host) is not None
            except ContractError as exc:
                # A broken host file (symlink, non-UTF-8, malformed markers) is a reportable
                # invalid state, never a crash - the dashboard must be able to show it.
                host_problem = f"{relative}: {str(exc).replace(str(root), '<repo>')}"
    has_surface = has_repodocs or has_managed_host or any(
        os.path.lexists(root / relative)
        for relative in (CONFIG_PATH, MANIFEST_PATH, "PROJECT_CONTEXT.md")
    )
    if host_problem is not None:
        return ("invalid", host_problem) if has_surface else ("absent", None)
    if not has_surface:
        return "absent", None
    try:
        validate_project(root)
    except ContractError as exc:
        return "invalid", str(exc).replace(str(root), "<repo>")
    return "valid", None


_INSTRUCTION_BASENAMES = {"agents.md", "claude.md", "claude.local.md", "agents.project-context.md"}
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
_SKILLS_ROOTS = (".github/skills/", ".claude/skills/", ".agents/skills/", ".codex/skills/", ".cursor/skills/")


def _is_agent_instruction(path: str) -> bool:
    lowered = path.lower()
    if lowered.rsplit("/", 1)[-1] in _INSTRUCTION_BASENAMES:
        return True
    if lowered in _INSTRUCTION_CONFIG_FILES:
        return True
    if lowered.startswith(".cursor/rules/") or lowered.startswith(".cursor/commands/") or lowered == ".cursor/commands":
        return True
    for skills_root in _SKILLS_ROOTS:
        if lowered.startswith(skills_root) and lowered.endswith(".md"):
            # One logical unit per vendored skill: its SKILL.md (any depth) or a file at the
            # skills root. Internal reference files would flood the map fifty entries deep;
            # their divergence still surfaces through the skill-directory aggregate hash.
            remainder = lowered[len(skills_root):]
            return remainder.rsplit("/", 1)[-1] == "skill.md" or "/" not in remainder
    return False


def _instruction_hosts(path: str) -> list[str]:
    lowered = path.lower()
    basename = lowered.rsplit("/", 1)[-1]
    hosts: list[str] = []
    # Basename rules apply only outside other hosts' directories: .cursor/commands/agents.md
    # is a cursor command that happens to be named agents, not a codex file.
    foreign = lowered.startswith((".cursor/", ".github/"))
    if (basename in {"claude.md", "claude.local.md"} and not foreign) or lowered.startswith(".claude/"):
        hosts.append("claude")
    if (basename in {"agents.md", "agents.project-context.md"} and not foreign) or lowered.startswith((".codex/", ".agents/")):
        hosts.append("codex")
    if lowered.startswith(".cursor/"):
        hosts.append("cursor")
    if lowered.startswith(".github/"):
        hosts.append("github")
    return hosts or ["unknown"]


def _skill_directory_digest(root: Path, skill_md: str) -> str | None:
    """Aggregate hash over a vendored skill directory, so divergence in any of its files
    (not only SKILL.md) surfaces on the one entry that represents the skill."""
    lowered = skill_md.lower()
    for skills_root in _SKILLS_ROOTS:
        if lowered.startswith(skills_root) and lowered.rsplit("/", 1)[-1] == "skill.md":
            skill_dir = (root / skill_md).parent
            lines: list[str] = []
            for directory, dirnames, filenames in os.walk(skill_dir, followlinks=False):
                dirnames[:] = sorted(d for d in dirnames if not _is_symlink_or_junction(Path(directory) / d))
                for name in sorted(filenames):
                    file_path = Path(directory) / name
                    if _is_symlink_or_junction(file_path) or not file_path.is_file():
                        continue
                    relative = file_path.relative_to(skill_dir).as_posix()
                    try:
                        lines.append(f"{relative}:{sha256_bytes(_read_regular(file_path, relative))}")
                    except ContractError:
                        continue
                    if len(lines) == 400:
                        return sha256_text("\n".join([*lines, "truncated"]))
            return sha256_text("\n".join(lines))
    return None


def _agent_instruction_map(root: Path) -> tuple[list[dict[str, Any]], bool]:
    """Inventory every file that can instruct an agent. Discovery honours the repository's
    own ignore rules, except the root host files and host-configuration directories, which
    are always checked - an ignore rule must never hide an instruction file from the map."""
    try:
        tracked_result = _git(root, "ls-files", "-z")
        others_result = _git(root, "ls-files", "-z", "--others", "--exclude-standard")
    except ContractError:
        return [], True
    tracked = {p for p in tracked_result.stdout.split("\0") if p} if tracked_result.returncode == 0 else set()
    others = {p for p in others_result.stdout.split("\0") if p} if others_result.returncode == 0 else set()
    # Git-ignored instruction files are still loaded by the hosts (ignoring CLAUDE.local.md is
    # the documented recommendation), so they must not hide from the map. The pathspecs bound
    # the listing to instruction basenames - a bare --ignored listing would return node_modules.
    try:
        ignored_result = _git(
            root, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--",
            ":(icase)*agents.md", ":(icase)*claude.md", ":(icase)*claude.local.md", ":(icase)*agents.project-context.md",
            # Dependencies increasingly ship their own CLAUDE.md/AGENTS.md; vendor trees are a
            # listing bound here (they would evict repo-local entries from the 200 cap), not an
            # audit-scope decision.
            ":(exclude)node_modules/", ":(exclude)vendor/", ":(exclude)third_party/",
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
        candidates.update(name for name in os.listdir(root) if name.lower() in _INSTRUCTION_BASENAMES)
    except OSError:
        pass
    for extra in (".claude", ".agents", ".codex", ".cursor", ".github"):
        base = root / extra
        if base.is_dir() and not _is_symlink_or_junction(base):
            for directory, dirnames, filenames in os.walk(base, followlinks=False):
                dirnames[:] = [d for d in dirnames if not _is_symlink_or_junction(Path(directory) / d)]
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
                root, "-c", "core.excludesfile=", "check-ignore", "-z", "--", *untracked_candidates
            )
            if check_result.returncode in (0, 1):
                ignored = {p for p in check_result.stdout.split("\0") if p}
        except ContractError:
            pass
    entries: list[dict[str, Any]] = []
    truncated = False
    for relative in sorted(candidates):
        path = root / relative
        if not path.is_file() or _is_symlink_or_junction(path):
            continue
        try:
            raw = _read_regular(path, relative)
            modified = datetime.fromtimestamp(path.lstat().st_mtime, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        except (ContractError, OSError):
            continue
        entries.append(
            {
                "path": relative,
                "tracked": relative in tracked,
                "ignored": relative in ignored,
                "size": len(raw),
                "sha256": _skill_directory_digest(root, relative) or sha256_bytes(raw),
                "modified": modified,
                "hosts": _instruction_hosts(relative),
            }
        )
        if len(entries) == 200:
            truncated = True
            break
    return entries, truncated


def _scope_review(root: Path, exclusions: list[str]) -> list[dict[str, Any]]:
    """Cross-check configured exclusions against git without changing what gets scanned."""
    review: list[dict[str, Any]] = []
    for excluded in exclusions:
        try:
            # :(literal) disables pathspec magic and globbing, matching _covered_by's
            # literal semantics; validate_config also rejects glob/magic characters.
            ls_result = _git(root, "ls-files", "-z", "--", f":(literal){excluded}")
            tracked_files = [p for p in ls_result.stdout.split("\0") if p] if ls_result.returncode == 0 else []
            # --no-index: pure pattern matching, so a TRACKED path that ignore rules also match
            # is reported - that combination is a no-op ignore and usually a forgotten git rm --cached.
            # The empty core.excludesfile neutralises the user's personal global ignores: only the
            # repository's own .gitignore and .git/info/exclude count as an inconsistency.
            ignored = (
                _git(root, "-c", "core.excludesfile=", "check-ignore", "-q", "--no-index", "--", excluded).returncode == 0
            )
        except ContractError:
            continue
        review.append(
            {
                "path": excluded,
                "tracked_files": len(tracked_files),
                "tracked_and_ignored": bool(tracked_files) and ignored,
                "agent_instruction_files": sorted(p for p in tracked_files if _is_agent_instruction(p))[:20],
            }
        )
    return review


_DECISION_ID_RE = re.compile(r"(?<![A-Za-z0-9])(ADR|MB)-[0-9]+(?![0-9])")


def _decision_citations(root: Path) -> list[dict[str, Any]]:
    """Existing ADR/MB citations in tracked files outside repodocs/; generation must not collide with them."""
    try:
        # -z separates the path from the matched line with NUL, so paths containing ':' parse
        # exactly; ids are re-extracted in Python with word boundaries, so prose like
        # "512MB-4GB" or "LOADR-9" never registers as an occupied decision id.
        result = _git(root, "grep", "-I", "-z", "-E", r"(ADR|MB)-[0-9]+", "--", ".", ":(exclude)repodocs")
    except ContractError:
        return []
    if result.returncode != 0:
        return []
    citations: dict[str, set[str]] = {}
    for line in result.stdout.splitlines():
        path, separator, content = line.partition("\0")
        if not separator or not path:
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
        path = root / relative
        if os.path.lexists(path):
            try:
                safe_path(root, relative, must_exist=True)
            except ContractError as exc:
                host_errors.append({"path": relative, "error": str(exc).replace(str(root), "<repo>")})
    repodocs = root / "repodocs"
    if repodocs.exists():
        if not repodocs.is_dir():
            host_errors.append({"path": "repodocs", "error": "repodocs must be a directory"})
        else:
            for directory, names, files in os.walk(repodocs, followlinks=False):
                offender = next(
                    (name for name in [*names, *files] if _is_symlink_or_junction(Path(directory) / name)), None
                )
                if offender is not None:
                    host_errors.append(
                        {
                            "path": (Path(directory) / offender).relative_to(root).as_posix(),
                            "error": "symlinks are not allowed anywhere under repodocs",
                        }
                    )
                    break
    for host, relative in HOST_FILES.items():
        path = root / relative
        if any(entry["path"] == relative for entry in host_errors):
            continue  # already reported by the fixed-path check; one row per path
        if path.is_file():
            try:
                extract_host_block(_decode_text(_read_regular(path, relative), relative), host)
            except ContractError as exc:
                host_errors.append({"path": relative, "error": str(exc).replace(str(root), "<repo>")})
    legacy = [
        relative
        for relative in (
            "project-context.config.yaml",
            "repodocs/project-context.config.yaml",
            "repodocs/audit/inventory.yaml",
            "docs/project-context.config.yaml",
            ".codex/skills/project-context",
        )
        if os.path.lexists(root / relative)
    ]
    # v0.1 root agents files: check the exact directory listing, not lexists, so a
    # case-insensitive filesystem never mistakes v0.2's AGENTS.md for legacy agents.md.
    root_entries = set(os.listdir(root))
    legacy.extend(name for name in ("agents.md", "agents.project-context.md") if name in root_entries)
    findings_dir = root / "repodocs/audit/findings"
    if findings_dir.is_dir():
        legacy.extend(sorted(path.relative_to(root).as_posix() for path in findings_dir.glob("*.yaml")))
    revision_result = _git(root, "rev-parse", "--verify", "HEAD")
    revision = revision_result.stdout.strip() if revision_result.returncode == 0 else None
    if revision is not None and not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision):
        raise ContractError("Git returned an invalid revision")
    context_state, context_error = _context_state(root)
    if host_errors and context_state == "valid":
        context_state = "invalid"
        context_error = f"{host_errors[0]['path']}: {host_errors[0]['error']}"
    evidence = _source_evidence(root)
    worktree_clean = _source_worktree_clean(root)
    exclusions: list[str] = []
    config_path = root / CONFIG_PATH
    if config_path.is_file():
        try:
            config = validate_config(
                strict_json_loads(_decode_text(_read_regular(config_path, CONFIG_PATH), CONFIG_PATH), CONFIG_PATH)
            )
            exclusions = list(config["audit"]["exclude"])
        except ContractError:
            exclusions = []  # an invalid config is already reported through context_state
    instruction_map, instructions_truncated = _agent_instruction_map(root)
    for entry in instruction_map:
        entry["in_excluded_scope"] = _covered_by(entry["path"], exclusions)
    effective_skill = _resolve_existing(skill_root or Path(__file__).resolve().parents[1], "skill root")
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
        "config_exists": (root / CONFIG_PATH).is_file(),
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
            if re.fullmatch(rf" {{0,3}}{re.escape(fence[0])}{{{fence[1]},}}[ \t]*", line):
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
            if value[index] == "]" and index + 1 < len(value) and value[index + 1] in "([":
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
    config = validate_config(strict_json_loads(_decode_text(config_raw, CONFIG_PATH), CONFIG_PATH))
    manifest = validate_manifest(strict_json_loads(_decode_text(manifest_raw, MANIFEST_PATH), MANIFEST_PATH))
    skill_version = (Path(__file__).resolve().parents[1] / "VERSION").read_text(encoding="utf-8").strip()
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
            manifest_newer = int(manifest["skill_version"].split(".")[2]) > int(skill_version.split(".")[2])
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
        raise ContractError("manifest config_sha256 does not match normalized config text")
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
            host = next((name for name, filename in HOST_FILES.items() if filename == artifact["path"]), None)
            if host is None or host not in manifest["hosts"]:
                raise ContractError(f"unexpected managed host artifact: {artifact['path']}")
            block = extract_host_block(_decode_text(raw, artifact["path"]), host)
            if block is None:
                raise ContractError(f"managed block is missing from {artifact['path']}")
            if block.replace("\r\n", "\n").replace("\r", "\n") != _host_block(host):
                raise ContractError(f"managed block content drifted in {artifact['path']}")
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
            raise ContractError(f"artifact hash mismatch: {artifact['path']} (expected {hash_subject})")
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
        raise ContractError("manifest audit_inventory id must map to repodocs/audit/inventory.json")
    inventory = validate_inventory(
        strict_json_loads(
            _decode_text(artifact_bytes["audit_inventory"], inventory_artifact["path"]),
            inventory_artifact["path"],
        )
    )
    latest_run = inventory["runs"][-1]
    generated_paths = {CONFIG_PATH, MANIFEST_PATH, *(artifact["path"] for artifact in manifest["artifacts"])}
    actual_source_state = "codebase" if _source_evidence(root, generated_paths) else "greenfield"
    if latest_run["source_state"] != actual_source_state:
        raise ContractError("latest inventory source_state does not match the repository")
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
        if artifact is None or artifact["path"] != artifact_path or artifact["kind"] != "owned_file":
            raise ContractError(f"manifest is missing required artifact: {artifact_path}")
    for artifact_id, artifact_path in topic_artifacts.items():
        artifact = artifacts_by_id.get(artifact_id)
        if config["document_layout"] == "full":
            if artifact is None or artifact["path"] != artifact_path or artifact["kind"] != "owned_file":
                raise ContractError(f"full layout requires {artifact_path}")
        elif artifact is not None:
            raise ContractError(f"compact layout must embed and omit {artifact_path}")
    approved_exclusions = set(config["audit"]["exclude"])
    recorded_exclusions = set(latest_run["scope"]["excluded"])
    implicit_exclusions = {"repodocs", ".agents/skills/project-context", ".claude/skills/project-context"}
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
            raise ContractError(f"manifest {domain} domain does not match latest inventory")
        configured = config["domains"][domain]
        if configured != "auto" and (configured == "enabled") != enabled:
            raise ContractError(f"configured {domain} domain does not match latest inventory")
        artifact = artifacts_by_id.get(artifact_id)
        if config["document_layout"] == "full" and enabled:
            if artifact is None or artifact["path"] != artifact_path or artifact["kind"] != "owned_file":
                raise ContractError(f"full layout requires {artifact_path} for enabled {domain}")
        elif artifact is not None:
            raise ContractError(f"{artifact_path} must be omitted for this layout/domain state")
    if config["document_layout"] == "compact":
        context = markdown["PROJECT_CONTEXT.md"]
        topics = ["stack", "architecture", "security", "testing", "edge-cases"]
        topics.extend(domain for domain in ("ui", "data") if domain in manifest_domains)
        for topic in topics:
            if f"[[context#{topic}]]" not in context:
                raise ContractError(f"compact context is missing canonical topic link: {topic}")
    project_map = validate_project_map(
        strict_json_loads(_decode_text(artifact_bytes["project_map"], PROJECT_MAP_PATH), PROJECT_MAP_PATH)
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
                    raise ContractError(f"planned project-map node needs ADR evidence: {node['id']}")
            elif not path_is_in_scope(item["path"], latest_scope):
                raise ContractError(f"project-map node evidence is outside latest completed scope: {node['id']}")
    nodes_by_id = {node["id"]: node for node in project_map["nodes"]}
    for edge in project_map["edges"]:
        planned_edge = any(nodes_by_id[node_id]["status"] == "planned" for node_id in (edge["from"], edge["to"]))
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
        if artifact is None or artifact["path"] != artifact_path or artifact["kind"] != "owned_file":
            raise ContractError(f"manifest is missing completed auditor findings: {auditor}")
        findings = validate_findings(
            strict_json_loads(_decode_text(artifact_bytes[artifact_id], artifact_path), artifact_path)
        )
        if findings["auditor"] != auditor:
            raise ContractError(f"findings auditor does not match artifact: {auditor}")
        if findings["run_id"] != latest_run["id"]:
            raise ContractError(f"findings run_id does not match latest audit run: {auditor}")
        for finding in findings["findings"]:
            if finding["status"] not in {"new", "persisting"}:
                continue
            if finding["kind"] in {"scope-inconsistency", "agent-directed-text"}:
                # scope_review-driven findings point inside a confirmed exclusion by design:
                # the exclusion is exactly what they report on.
                continue
            paths = [finding["identity"]["path"]]
            paths.extend(item["path"] for item in finding["evidence"])
            paths.extend(item["path"] for item in finding["verification"]["counterevidence"])
            for path in paths:
                if not path_is_in_scope(path, latest_scope) or not path_is_in_scope(path, findings["scope"]):
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
                raise ContractError(f"active finding lacks a disposition reference: {finding['id']}")
    wikilinks = 0
    wikilink_entries: list[dict[str, str]] = []
    artifact_ids_by_path = {artifact["path"]: artifact_id for artifact_id, artifact in artifacts_by_id.items()}
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
            {"source": source_id, "target": target, "fragment": fragment} for target, fragment in links
        )
        unknown_links = sorted({target for target, _ in links} - artifacts_by_id.keys())
        if unknown_links:
            raise ContractError(f"{relative} has unknown wikilinks: {', '.join(unknown_links)}")
        for target, fragment in links:
            if fragment:
                target_path = artifacts_by_id[target]["path"]
                target_text = markdown.get(target_path, "")
                if f'<a id="{fragment}"></a>' not in target_text:
                    raise ContractError(f"{relative} has unresolved wikilink anchor: {target}#{fragment}")
    # The dashboard machine-reads the "## ADR-NNN:" / "## MB-NNN:" heading shape; an anchor
    # without its heading renders that decision or backlog item invisible in the views.
    for governance_path, prefix in (("repodocs/decisions.md", "ADR"), ("repodocs/migration-backlog.md", "MB")):
        # Code spans stay quotable (SKILL.md's documented escape), and the heading regex
        # accepts exactly what _markdown_sections accepts, so the tripwire never fires on
        # content the dashboard actually renders.
        governance_text = strip_code_spans(markdown.get(governance_path, ""))
        headings = set(re.findall(rf"^##\s+({prefix}-[0-9]{{3,}})\s*(?::|$)", governance_text, flags=re.MULTILINE))
        for anchor in re.findall(rf'<a id="({prefix}-[0-9]{{3,}})"></a>', governance_text):
            if anchor not in headings:
                raise ContractError(
                    f"{governance_path} anchor {anchor} has no matching '## {anchor}: ...' heading "
                    "(the dashboard reads that literal heading shape, in any language)"
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
        "summary": summary,
    }


def validate_project(repo: Path) -> dict[str, Any]:
    return cast(dict[str, Any], _validated_project(repo)["summary"])


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
        body: list[str] = []
        for candidate in lines[index + 1 :]:
            if candidate.startswith("## "):
                break
            if candidate.strip() and not candidate.lstrip().startswith("<!--"):
                body.append(candidate.strip())
            if len(body) == 8:
                break
        meta = ""
        for line in body:
            found = re.search(r"Priority:\s*([^\s·]+)\s*·\s*Effort:\s*([^\s·]+)\s*·\s*Status:\s*(\S+)", line)
            if found:
                meta = f"{found.group(1)} · effort {found.group(2)} · {found.group(3)}"
                break
        sections.append({"id": identifier, "title": title, "summary": "\n".join(body), "lines": body, "meta": meta})
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
    content = {key: item for key, item in value.items() if key not in {"generated_at", "snapshot_id"}}
    canonical = json.dumps(content, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_REPODOCS_LINK_RE = re.compile(r"repodocs/[A-Za-z0-9_./-]+\.(?:md|json)(?:#[A-Za-z0-9_-]+)?")


def _instruction_view(root: Path, entries: list[dict[str, Any]], markdown: dict[str, str] | None) -> list[dict[str, Any]]:
    """Read-only enrichment for the dashboard: content preview plus repodocs link status.
    Instruction content is untrusted data; it is shown, never followed or summarised."""
    view: list[dict[str, Any]] = []
    for entry in entries:
        item = dict(entry)
        links: list[dict[str, str]] = []
        try:
            raw = _read_regular(root / entry["path"], entry["path"])
            text = raw[:65536].decode("utf-8", errors="replace")
            item["preview"] = text[:2000]
            for raw_link in sorted(set(_REPODOCS_LINK_RE.findall(text)))[:30]:
                target_path, _, fragment = raw_link.partition("#")
                if markdown is None:
                    status = "unverified"
                elif target_path not in markdown:
                    status = "dangling-file"
                elif fragment and f'<a id="{fragment}"></a>' not in markdown[target_path]:
                    status = "dangling-anchor"
                else:
                    status = "resolves"
                links.append({"raw": raw_link, "status": status})
        except ContractError:
            item["preview"] = ""
        item["links"] = links
        view.append(item)
    return view


def dashboard_snapshot(repo: Path) -> dict[str, Any]:
    """Build a read-only dashboard model from the same validated project contract."""
    current = preflight(repo)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    root = Path(current["root"])
    version = (Path(__file__).resolve().parents[1] / "VERSION").read_text(encoding="utf-8").strip()
    model: dict[str, Any] = {
        "schema_version": 1,
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
            "language": "en",
            "layout": None,
            "hosts": [],
            "domains": [],
        },
        "audit": {"latest": None, "history": []},
        "findings": [],
        "finding_summary": {"total": 0, "active": 0, "critical_high": 0},
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
                *(f"{entry['path']}: {entry['error']}" for entry in current["host_errors"]),
            ],
        },
    }
    if current["context_state"] != "valid":
        model["agent_instructions"] = _instruction_view(root, current["agent_instructions"], None)
        model["agent_instructions_truncated"] = current["agent_instructions_truncated"]
        model["snapshot_id"] = _snapshot_id(model)
        return model

    try:
        project = _validated_project(root)
    except ContractError as exc:
        model["context"]["state"] = "invalid"
        model["context"]["error"] = str(exc).replace(str(root), "<repo>")
        model["integrity"]["status"] = "invalid"
        model["agent_instructions"] = _instruction_view(root, current["agent_instructions"], None)
        model["agent_instructions_truncated"] = current["agent_instructions_truncated"]
        model["snapshot_id"] = _snapshot_id(model)
        return model

    config = project["config"]
    manifest = project["manifest"]
    latest = project["latest_run"]
    flattened: list[dict[str, Any]] = []
    for auditor, document in project["findings"].items():
        for finding in document["findings"]:
            verification = finding["verification"]
            flattened.append(
                {
                    **finding,
                    "auditor": auditor,
                    "source_severity": finding["severity"],
                    "effective_severity": verification.get("resulting_severity") or finding["severity"],
                    "identity_sha256": sha256_bytes(
                        json.dumps(
                            finding["identity"], ensure_ascii=False, separators=(",", ":"), sort_keys=True
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
    revision_state = _dashboard_revision_state(current, latest)
    model["project"]["revision_state"] = revision_state
    model["context"].update(
        {
            "language": config["language"],
            "layout": config["document_layout"],
            "hosts": manifest["hosts"],
            "domains": manifest["domains"],
            "warnings": project["summary"].get("warnings", []),
        }
    )
    model["audit"] = {"latest": latest, "history": list(reversed(project["inventory"]["runs"]))}
    model["findings"] = flattened
    model["finding_summary"] = {
        "total": len(flattened),
        "active": len(active),
        "critical_high": sum(item["effective_severity"] in {"critical", "high"} for item in active),
    }
    model["project_map"] = {
        "nodes": project["project_map"]["nodes"],
        "edges": project["project_map"]["edges"],
    }
    artifact_paths = {artifact_id: artifact["path"] for artifact_id, artifact in project["artifacts"].items()}
    model["context_map"] = {
        "nodes": [
            {
                "id": artifact_id,
                "label": artifact["path"],
                "group": "Context artifacts",
                "kind": artifact["kind"].replace("_", " "),
                "status": "current",
                "evidence": [{"path": artifact["path"], "detail": "Manifest-owned validated artifact."}],
            }
            for artifact_id, artifact in sorted(project["artifacts"].items())
        ],
        "edges": [
            {
                "from": link["source"],
                "to": link["target"],
                "label": f"links to #{link['fragment']}" if link["fragment"] else "links to",
                "evidence": [
                    {"path": artifact_paths[link["source"]], "detail": "Validated wikilink reference."}
                ],
            }
            for link in project["wikilinks"]
        ],
    }
    markdown = project["markdown"]
    model["documents"] = {
        "decisions": _markdown_sections(markdown["repodocs/decisions.md"], "ADR"),
        "debt": _markdown_sections(markdown["repodocs/LegacyWarning.md"]),
        "backlog": _markdown_sections(markdown["repodocs/migration-backlog.md"], "MB"),
        "drift": _markdown_sections(markdown["repodocs/audit/drift-report.md"]),
    }
    limitations = ["Structural validation does not prove factual completeness."]
    if revision_state == "unknown":
        limitations.append("Source freshness is unknown because the audit or current worktree is not cleanly anchored.")
    elif revision_state == "stale":
        limitations.append("Repository HEAD or worktree changed after the latest audit.")
    model["integrity"] = {
        "status": "valid",
        "artifacts": project["summary"]["artifacts"],
        "wikilinks": project["summary"]["wikilinks"],
        "checks": [
            {"name": "Manifest and hashes", "status": "passed"},
            {"name": "Audit run binding", "status": "passed"},
            {"name": "Independent completeness check", "status": latest["verification"]["blind"]},
            {"name": "HEAD matches audit", "status": revision_state},
        ],
        "limitations": limitations,
    }
    model["agent_instructions"] = _instruction_view(root, current["agent_instructions"], markdown)
    model["agent_instructions_truncated"] = current["agent_instructions_truncated"]
    model["snapshot_id"] = _snapshot_id(model)
    return model


def _dashboard_json(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return (
        encoded.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_dashboard_html(repo: Path, route: str = "/", nonce: str | None = None) -> bytes:
    if not re.fullmatch(r"/(?:[a-zA-Z0-9_-]+/)?", route):
        raise ContractError("dashboard route is invalid")
    nonce = nonce or secrets.token_urlsafe(18)
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", nonce):
        raise ContractError("dashboard CSP nonce is invalid")
    template_path = Path(__file__).resolve().parents[1] / "assets/dashboard.html"
    template = _decode_text(_read_regular(template_path, "assets/dashboard.html"), "assets/dashboard.html")
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


def serve_dashboard(repo: Path, *, open_browser: bool = True) -> None:
    root = _resolve_existing(repo, "repository path")
    # Validate the fixed root before opening a socket; requests can never choose another path.
    preflight(root)
    token = secrets.token_hex(16)
    route = f"/{token}/"

    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "ProjectContext/1"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _headers(self, status: int, length: int, nonce: str, content_type: str) -> None:
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
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.end_headers()

        def _page(self, *, head: bool = False) -> None:
            dashboard_server = cast(ThreadingHTTPServer, self.server)
            expected_host = f"127.0.0.1:{dashboard_server.server_port}"
            if self.headers.get("Host") not in {expected_host, f"localhost:{dashboard_server.server_port}"}:
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
            self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        do_GET = _page

        def do_HEAD(self) -> None:
            self._page(head=True)

        do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = do_CONNECT = _method_not_allowed

    server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
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
        "evals/cases.json",
        "evals/fixtures/build.sh",
        "evals/scorecard.template.json",
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
    required |= {f"auditors/{name}.md" for name in ("_common", "architecture", "bloat", "data", "security", "stack", "testing", "ui")}
    required |= {f"references/{name}.md" for name in ("decision-matrix", "diff-review", "findings-schema", "greenfield", "host-integration", "jcodemunch", "upgrade")}
    required |= {f"templates/{name}.md" for name in ("CurrentSprint", "LegacyWarning", "architecture", "data-model", "decisions", "drift-report", "edge-cases", "migration-backlog", "security", "techstack", "testing", "ui-kit")}
    missing = sorted(relative for relative in required if not (root / relative).is_file())
    if missing:
        raise ContractError(f"skill payload is missing: {', '.join(missing)}")
    for relative in sorted(required):
        if not _decode_text(_read_regular(root / relative, relative), relative).strip():
            raise ContractError(f"skill payload file is empty: {relative}")
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ContractError("VERSION must contain x.y.z")
    load_json(root / "schemas/findings.schema.json")
    load_json(root / "schemas/project-map.schema.json")
    validate_config(load_json(root / "templates/project-context.config.json"))
    validate_inventory(load_json(root / "templates/audit-inventory.json"))
    validate_project_map(load_json(root / "templates/project-map.json"))
    validate_project_map(load_json(root / "examples/project-map.json"))
    manifest = validate_manifest(load_json(root / "templates/project-context.manifest.json"))
    if manifest["skill_version"] != version:
        raise ContractError("template manifest skill_version does not match VERSION")
    validate_inventory(load_json(root / "examples/inventory.json"))
    scorecard = _mapping(load_json(root / "evals/scorecard.template.json"), "evals scorecard template")
    _exact_keys(scorecard, {"schema_version", "skill_version", "runs"}, "evals scorecard template")
    for index, raw in enumerate(_list(scorecard["runs"], "evals scorecard template runs")):
        run_entry = _mapping(raw, f"evals scorecard template runs[{index}]")
        _exact_keys(
            run_entry,
            {"case_id", "run_at", "passed", "expected_met", "expected_missed", "forbidden_hit", "notes"},
            f"evals scorecard template runs[{index}]",
        )
    cases = _mapping(load_json(root / "evals/cases.json"), "evals")
    _exact_keys(cases, {"schema_version", "cases"}, "evals")
    if type(cases["schema_version"]) is not int or cases["schema_version"] != 1:
        raise ContractError("evals.schema_version must be 1")
    case_ids: set[str] = set()
    for index, raw in enumerate(_list(cases["cases"], "evals.cases")):
        case = _mapping(raw, f"evals.cases[{index}]")
        _exact_keys(case, {"id", "request", "setup", "expected", "forbidden"}, f"evals.cases[{index}]")
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
    command = commands.add_parser("self-check")
    command.add_argument("--skill-root", type=Path, default=Path(__file__).resolve().parents[1])
    command = commands.add_parser("preflight")
    command.add_argument("--repo", required=True, type=Path)
    command.add_argument("--skill-root", type=Path, default=Path(__file__).resolve().parents[1])
    command = commands.add_parser("merge-host")
    command.add_argument("--host", required=True, choices=sorted(HOST_FILES))
    command.add_argument("--input", type=Path)
    command.add_argument("--expected-sha256")
    command.add_argument("--allow-create", action="store_true")
    command = commands.add_parser("validate-project")
    command.add_argument("--repo", required=True, type=Path)
    command = commands.add_parser("validate-config")
    command.add_argument("--input", required=True, type=Path)
    command = commands.add_parser("validate-findings")
    command.add_argument("--input", required=True, type=Path)
    command.add_argument("--previous", type=Path)
    command.add_argument("--previous-sha256", help="expected sha256:<hex> of the previous file's normalized text, from the last valid manifest")
    command.add_argument("--allow-provisional", action="store_true")
    command = commands.add_parser("validate-inventory")
    command.add_argument("--input", required=True, type=Path)
    command.add_argument("--previous", type=Path)
    command = commands.add_parser("validate-project-map")
    command.add_argument("--input", required=True, type=Path)
    command = commands.add_parser("validate-manifest")
    command.add_argument("--input", required=True, type=Path)
    command = commands.add_parser("dashboard")
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
            if args.input and args.input.exists() and not args.input.is_file():
                raise ContractError("merge-host --input must be a regular file")
            raw = args.input.read_bytes() if args.input and args.input.exists() else b""
            if not raw and not args.allow_create:
                # A same-file shell redirect truncates the input before Python reads it;
                # without this guard that silently erases the user's host file.
                raise ContractError(
                    "merge-host input is missing or empty; pass --allow-create only for a genuinely new file, "
                    "and never redirect onto the input file in the same command"
                )
            bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
            text = _decode_text(raw, str(args.input or "host input"))
            merged = merge_host_text(text, args.host, args.expected_sha256)
            sys.stdout.buffer.write(bom + merged.encode("utf-8"))
        elif args.command == "validate-project":
            dump(validate_project(args.repo))
        elif args.command == "validate-config":
            validate_config(load_json(args.input))
            dump({"status": "valid"})
        elif args.command == "validate-findings":
            if args.previous_sha256 and not args.previous:
                raise ContractError("--previous-sha256 requires --previous")
            if args.previous and args.previous_sha256:
                if not HASH_RE.fullmatch(args.previous_sha256):
                    raise ContractError("--previous-sha256 is not a valid sha256:<hex> value")
                try:
                    previous_raw = args.previous.read_bytes()
                except OSError as exc:
                    raise ContractError(f"cannot read --previous file {args.previous}: {exc.strerror or exc}") from exc
                # The manifest hashes normalized text (BOM stripped, line endings unified),
                # so the guard must too - otherwise a CRLF checkout rejects genuine history.
                if sha256_text(_decode_text(previous_raw, str(args.previous))) != args.previous_sha256:
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
            inventory = validate_inventory(load_json(args.input), load_json(args.previous) if args.previous else None)
            dump({"status": "valid", "runs": len(inventory["runs"])})
        elif args.command == "validate-project-map":
            project_map = validate_project_map(load_json(args.input))
            dump({"status": "valid", "nodes": len(project_map["nodes"]), "edges": len(project_map["edges"])})
        elif args.command == "validate-manifest":
            manifest = validate_manifest(load_json(args.input))
            dump({"status": "valid", "artifacts": len(manifest["artifacts"])})
        elif args.command == "dashboard":
            serve_dashboard(args.repo, open_browser=not args.no_open)
        return 0
    except ContractError as exc:
        print(json.dumps({"error": str(exc), "code": exc.code}, ensure_ascii=False), file=sys.stderr)
        return exc.code
    except Exception as exc:  # Keep CLI failures stable without hiding programmer errors in tests.
        print(json.dumps({"error": f"unexpected validator failure ({type(exc).__name__})", "code": 70}), file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
