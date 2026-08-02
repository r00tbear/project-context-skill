"""Small stdlib validator for project-context generated files."""

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any


CONFIG_PATH = "repodocs/project-context.config.json"
MANIFEST_PATH = "repodocs/project-context.manifest.json"
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
CORE_DOMAINS = {"architecture", "stack", "security", "testing"}
ALL_DOMAINS = CORE_DOMAINS | {"ui", "data"}
ARTIFACT_KINDS = {"owned_file", "managed_block"}
SEVERITIES = {"low", "medium", "high", "critical"}
SEVERITY_RANK = {name: rank for rank, name in enumerate(("low", "medium", "high", "critical"))}
CORE_AUDITORS = {"stack", "architecture", "bloat", "security", "testing"}


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
                mode = os.lstat(candidate).st_mode
            except OSError as exc:
                raise ContractError(f"cannot inspect {rel}: {exc.strerror or exc}") from exc
            if stat.S_ISLNK(mode):
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
        validate_relative_path(path, f"config.audit.exclude[{index}]")
    return config


def _validate_scope(value: Any, label: str) -> dict[str, Any]:
    scope = _mapping(value, label)
    _exact_keys(scope, {"included", "excluded", "unscanned"}, label)
    for key in ("included", "excluded", "unscanned"):
        paths = _unique_strings(scope[key], f"{label}.{key}")
        for index, path in enumerate(paths):
            if path != ".":
                validate_relative_path(path, f"{label}.{key}[{index}]")
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
    _exact_keys(document, {"schema_version", "auditor", "scanned_at", "scope", "findings"}, "findings document")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ContractError("findings.schema_version must be 1")
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
                    raise ContractError(f"resolved finding was outside comparable completed scope: {item['id']}")
    return document


def validate_inventory(value: Any, previous: Any | None = None) -> dict[str, Any]:
    inventory = _mapping(value, "inventory")
    _exact_keys(inventory, {"schema_version", "runs"}, "inventory")
    if type(inventory["schema_version"]) is not int or inventory["schema_version"] != 1:
        raise ContractError("inventory.schema_version must be 1")
    runs = _list(inventory["runs"], "inventory.runs")
    if not runs:
        raise ContractError("inventory.runs must not be empty")
    run_ids: set[str] = set()
    for index, raw in enumerate(runs):
        label = f"inventory.runs[{index}]"
        run = _mapping(raw, label)
        _exact_keys(
            run,
            {"id", "scanned_at", "source_state", "outcome", "domains", "coverage", "scope", "tools"},
            label,
        )
        run_id = _text(run["id"], f"{label}.id")
        if run_id in run_ids:
            raise ContractError(f"duplicate audit run id: {run_id}")
        run_ids.add(run_id)
        _timestamp(run["scanned_at"], f"{label}.scanned_at")
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
        expected = "failed" if failed else (
            "coverage-incomplete"
            if scope["unscanned"] or "unknown" in domains.values() or set(completed) != set(required)
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
            ["git", "-C", str(root), *args],
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


def _source_evidence(root: Path) -> list[str]:
    evidence: list[str] = []
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
            if (Path(directory) / name).is_symlink():
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
            if path.is_symlink():
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


def _resolve_existing(path: Path, label: str) -> Path:
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise ContractError(f"{label} does not exist: {path}") from exc


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
            safe_path(root, relative, must_exist=True)
    repodocs = root / "repodocs"
    if repodocs.exists():
        if not repodocs.is_dir():
            raise ContractError("repodocs must be a directory")
        for directory, names, files in os.walk(repodocs, followlinks=False):
            if any((Path(directory) / name).is_symlink() for name in [*names, *files]):
                raise ContractError("symlinks are not allowed anywhere under repodocs")
    for host, relative in HOST_FILES.items():
        path = root / relative
        if path.is_file():
            extract_host_block(_decode_text(_read_regular(path, relative), relative), host)
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
    evidence = _source_evidence(root)
    effective_skill = _resolve_existing(skill_root or Path(__file__).resolve().parents[1], "skill root")
    return {
        "status": "safe",
        "root": str(root),
        "git_root": str(git_root),
        "revision": revision,
        "source_state": "codebase" if evidence else "greenfield",
        "source_evidence": evidence,
        "canonical_config": CONFIG_PATH,
        "config_exists": (root / CONFIG_PATH).is_file(),
        "legacy_surfaces": legacy,
        "skill_root": str(effective_skill),
    }


def validate_project(repo: Path) -> dict[str, Any]:
    root = _resolve_existing(repo, "repository path")
    config_path = safe_path(root, CONFIG_PATH, must_exist=True)
    manifest_path = safe_path(root, MANIFEST_PATH, must_exist=True)
    config_raw = _read_regular(config_path, CONFIG_PATH)
    manifest_raw = _read_regular(manifest_path, MANIFEST_PATH)
    config = validate_config(strict_json_loads(_decode_text(config_raw, CONFIG_PATH), CONFIG_PATH))
    manifest = validate_manifest(strict_json_loads(_decode_text(manifest_raw, MANIFEST_PATH), MANIFEST_PATH))
    skill_version = (Path(__file__).resolve().parents[1] / "VERSION").read_text(encoding="utf-8").strip()
    if manifest["skill_version"] != skill_version:
        raise ContractError("manifest skill_version does not match the installed skill")
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
            raise ContractError(f"artifact hash mismatch: {artifact['path']}")
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
    common_artifacts = {
        "decisions": "repodocs/decisions.md",
        "legacy_warning": "repodocs/LegacyWarning.md",
        "migration_backlog": "repodocs/migration-backlog.md",
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
    wikilinks = 0
    for relative, text in markdown.items():
        tokens = WIKILINK_TOKEN_RE.findall(text)
        if text.count("[[") != len(tokens) or text.count("]]") != len(tokens):
            raise ContractError(f"{relative} has malformed wikilink markers")
        links = WIKILINK_RE.findall(text)
        if len(links) != len(tokens):
            raise ContractError(f"{relative} has malformed wikilinks")
        wikilinks += len(links)
        unknown_links = sorted({target for target, _ in links} - artifacts_by_id.keys())
        if unknown_links:
            raise ContractError(f"{relative} has unknown wikilinks: {', '.join(unknown_links)}")
        for target, fragment in links:
            if fragment:
                target_path = artifacts_by_id[target]["path"]
                target_text = markdown.get(target_path, "")
                if f'<a id="{fragment}"></a>' not in target_text:
                    raise ContractError(f"{relative} has unresolved wikilink anchor: {target}#{fragment}")
    return {
        "status": "valid",
        "artifacts": len(manifest["artifacts"]),
        "hosts": manifest["hosts"],
        "domains": manifest["domains"],
        "wikilinks": wikilinks,
    }


def self_check(skill_root: Path) -> dict[str, Any]:
    root = _resolve_existing(skill_root, "skill root")
    required = {
        "SKILL.md",
        "README.md",
        "LICENSE",
        "VERSION",
        "agents/openai.yaml",
        "scripts/project_context.py",
        "evals/cases.json",
        "schemas/findings.schema.json",
        "examples/PROJECT_CONTEXT.md",
        "examples/inventory.json",
        "templates/PROJECT_CONTEXT.md",
        "templates/project-context.config.json",
        "templates/project-context.manifest.json",
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
    validate_config(load_json(root / "templates/project-context.config.json"))
    manifest = validate_manifest(load_json(root / "templates/project-context.manifest.json"))
    if manifest["skill_version"] != version:
        raise ContractError("template manifest skill_version does not match VERSION")
    validate_inventory(load_json(root / "examples/inventory.json"))
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
    command.add_argument("--allow-provisional", action="store_true")
    command = commands.add_parser("validate-inventory")
    command.add_argument("--input", required=True, type=Path)
    command.add_argument("--previous", type=Path)
    command = commands.add_parser("validate-manifest")
    command.add_argument("--input", required=True, type=Path)
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
            current = validate_findings(
                load_json(args.input),
                load_json(args.previous) if args.previous else None,
                allow_provisional=args.allow_provisional,
            )
            dump({"status": "valid", "findings": len(current["findings"])})
        elif args.command == "validate-inventory":
            inventory = validate_inventory(load_json(args.input), load_json(args.previous) if args.previous else None)
            dump({"status": "valid", "runs": len(inventory["runs"])})
        elif args.command == "validate-manifest":
            manifest = validate_manifest(load_json(args.input))
            dump({"status": "valid", "artifacts": len(manifest["artifacts"])})
        return 0
    except ContractError as exc:
        print(json.dumps({"error": str(exc), "code": exc.code}, ensure_ascii=False), file=sys.stderr)
        return exc.code
    except Exception as exc:  # Keep CLI failures stable without hiding programmer errors in tests.
        print(json.dumps({"error": f"unexpected validator failure ({type(exc).__name__})", "code": 70}), file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
