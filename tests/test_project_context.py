import copy
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from collections import Counter
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from scripts.project_context import (
    HOST_MARKERS,
    ContractError,
    _dashboard_handler_class,
    _instruction_view,
    _markdown_sections,
    dashboard_snapshot,
    extract_host_block,
    merge_host_text,
    preflight,
    render_dashboard_html,
    safe_path,
    self_check,
    sha256_bytes,
    sha256_text,
    strict_json_loads,
    strip_code_spans,
    validate_config,
    validate_findings,
    validate_inventory,
    validate_manifest,
    validate_project,
    validate_project_map,
    validate_relative_path,
)


ROOT = Path(__file__).resolve().parents[1]


def _symlinks_supported() -> bool:
    try:
        with tempfile.TemporaryDirectory() as probe:
            os.symlink(os.path.join(probe, "target"), os.path.join(probe, "link"))
        return True
    except (OSError, NotImplementedError, AttributeError):
        return False


requires_symlinks = unittest.skipUnless(_symlinks_supported(), "symlink creation is unavailable on this platform")
SCRIPT = ROOT / "scripts/project_context.py"
SKILL_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
RUN_ID = "run-20260802-1"
AUDIT_REVISION = "a" * 40


def config() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "user_level": "specialist",
        "language": "en-US",
        "document_layout": "full",
        "domains": {"ui": "disabled", "data": "disabled"},
        "hosts": {"claude": True, "codex": True},
        "audit": {"exclude": []},
    }


def finding_document(severity: str = "medium", run_id: str = RUN_ID) -> dict[str, Any]:
    verification = "confirmed" if severity in {"high", "critical"} else "not-required"
    return {
        "schema_version": 2,
        "run_id": run_id,
        "auditor": "architecture",
        "scanned_at": "2026-08-02T12:00:00Z",
        "scope": {"included": ["."], "excluded": [], "unscanned": []},
        "findings": [
            {
                "id": "architecture-001",
                "kind": "boundary",
                "title": "Mixed responsibilities",
                "severity": severity,
                "confidence": "high",
                "identity": {"path": "src/module", "assertion": "Responsibilities are mixed"},
                "status": "new",
                "evidence": [{"path": "src/module", "line": 4, "detail": "Two responsibilities meet here."}],
                "verification": {
                    "status": verification,
                    "resulting_severity": severity,
                    "counterevidence": [],
                    "note": "Independently checked." if verification == "confirmed" else "Low impact.",
                },
            }
        ],
    }


def inventory(
    revision: str | None = AUDIT_REVISION,
    worktree_clean: bool | None = True,
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "runs": [
            {
                "id": RUN_ID,
                "scanned_at": "2026-08-02T12:00:00+00:00",
                "revision": revision,
                "worktree_clean": worktree_clean,
                "source_state": "codebase",
                "outcome": "complete",
                "domains": {"ui": "absent", "data": "absent"},
                "coverage": {
                    "required": ["stack", "architecture", "bloat", "security", "testing"],
                    "completed": ["stack", "architecture", "bloat", "security", "testing"],
                    "skipped": {"ui": "No interactive surface", "data": "No persisted/shared contract"},
                    "failed": [],
                },
                "scope": {"included": ["."], "excluded": [], "unscanned": []},
                "tools": {"jcodemunch": "used"},
                "verification": {"blind": "passed", "issues": 0},
            }
        ],
    }


def project_map(run_id: str = RUN_ID) -> dict[str, Any]:
    evidence = [{"path": "src/module", "line": 4, "detail": "Repository evidence."}]
    return {
        "schema_version": 1,
        "run_id": run_id,
        "nodes": [
            {
                "id": "module",
                "label": "Module",
                "kind": "component",
                "status": "current",
                "evidence": evidence,
            }
        ],
        "edges": [],
    }


class StrictJsonTests(unittest.TestCase):
    def test_rejects_duplicate_keys_at_any_depth(self) -> None:
        with self.assertRaisesRegex(ContractError, "duplicate JSON key"):
            strict_json_loads('{"outer":{"x":1,"x":2}}')

    def test_rejects_non_finite_numbers(self) -> None:
        for token in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(token=token), self.assertRaisesRegex(ContractError, "non-finite"):
                strict_json_loads('{"value":' + token + "}")

    def test_accepts_normal_json(self) -> None:
        self.assertEqual({"a": [1, True, None]}, strict_json_loads('{"a":[1,true,null]}'))


class PathTests(unittest.TestCase):
    def test_rejects_unsafe_relative_paths(self) -> None:
        for value in (".", "../x", "a/../x", "a/./x", "/tmp/x", "a//x", "a\\x", ".git/config", "a\nfile"):
            with self.subTest(value=value), self.assertRaises(ContractError):
                validate_relative_path(value)

    @unittest.skipUnless(os.name == "nt", "NTFS junctions exist only on Windows")
    def test_safe_path_rejects_ntfs_junction(self) -> None:
        import _winapi
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target = base / "real"
            target.mkdir()
            repo = base / "repo"
            repo.mkdir()
            _winapi.CreateJunction(str(target), str(repo / "junction"))
            with self.assertRaisesRegex(ContractError, "symlink is not allowed"):
                safe_path(repo, "junction/file.md")

    @requires_symlinks
    def test_safe_path_rejects_symlink_components(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root.parent / f"{root.name}-outside"
            outside.mkdir()
            try:
                (root / "repodocs").symlink_to(outside, target_is_directory=True)
                with self.assertRaisesRegex(ContractError, "symlink"):
                    safe_path(root, "repodocs/context.md")
            finally:
                outside.rmdir()

    def test_safe_path_returns_normal_child(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(root.resolve() / "repodocs/context.md", safe_path(root, "repodocs/context.md"))


class DocumentValidationTests(unittest.TestCase):
    def test_config_template_and_fixture_are_valid(self) -> None:
        self.assertEqual(1, validate_config(config())["schema_version"])
        template = strict_json_loads((ROOT / "templates/project-context.config.json").read_text())
        validate_config(template)

    def test_config_rejects_non_boolean_host_and_unknown_key(self) -> None:
        value = config()
        value["hosts"]["claude"] = "yes"
        with self.assertRaisesRegex(ContractError, "booleans"):
            validate_config(value)
        value = config()
        value["future"] = True
        with self.assertRaisesRegex(ContractError, "unknown keys"):
            validate_config(value)

    def test_schema_versions_reject_json_booleans(self) -> None:
        values_and_validators = (
            (config(), validate_config),
            (finding_document(), validate_findings),
            (inventory(), validate_inventory),
            (project_map(), validate_project_map),
            (strict_json_loads((ROOT / "templates/project-context.manifest.json").read_text()), validate_manifest),
        )
        for value, validator in values_and_validators:
            with self.subTest(validator=validator.__name__), self.assertRaisesRegex(ContractError, "schema_version"):
                value["schema_version"] = True
                validator(value)

    def test_findings_validate_and_preserve_ids(self) -> None:
        previous = finding_document()
        current = copy.deepcopy(previous)
        current["findings"][0]["status"] = "resolved"
        validate_findings(current, previous)
        current["findings"] = []
        with self.assertRaisesRegex(ContractError, "removed"):
            validate_findings(current, previous)

    def test_resolution_requires_comparable_completed_scope(self) -> None:
        previous = finding_document()
        current = copy.deepcopy(previous)
        current["findings"][0]["status"] = "resolved"
        current["scope"]["included"] = ["other"]
        with self.assertRaisesRegex(ContractError, "comparable completed scope"):
            validate_findings(current, previous)
        previous = finding_document()
        previous["scope"]["included"] = ["other"]
        current = copy.deepcopy(previous)
        current["scope"]["included"] = ["."]
        current["findings"][0]["status"] = "resolved"
        with self.assertRaisesRegex(ContractError, "comparable completed scope"):
            validate_findings(current, previous)

    def test_finding_identity_is_stable_and_id_matches_auditor(self) -> None:
        previous = finding_document()
        current = copy.deepcopy(previous)
        current["findings"][0]["identity"]["assertion"] = "A different claim"
        with self.assertRaisesRegex(ContractError, "assertion changed .* copy identity byte for byte"):
            validate_findings(current, previous)
        current = copy.deepcopy(previous)
        current["findings"][0]["identity"]["path"] = "src/other"
        current["findings"][0]["evidence"][0]["path"] = "src/other"
        with self.assertRaisesRegex(ContractError, "reused"):
            validate_findings(current, previous)
        previous["findings"][0]["id"] = "testing-001"
        with self.assertRaisesRegex(ContractError, "prefixed"):
            validate_findings(previous)

    def test_high_finding_requires_adversarial_verification(self) -> None:
        value = finding_document("critical")
        value["findings"][0]["verification"]["status"] = "not-required"
        with self.assertRaisesRegex(ContractError, "adversarial"):
            validate_findings(value)

    def test_pending_verification_is_provisional_only(self) -> None:
        value = finding_document("high")
        value["findings"][0]["verification"].update(status="pending", resulting_severity=None, note="Awaiting review.")
        with self.assertRaisesRegex(ContractError, "pending verification"):
            validate_findings(value)
        validate_findings(value, allow_provisional=True)

    def test_refuted_finding_requires_counterevidence(self) -> None:
        value = finding_document()
        value["findings"][0]["verification"]["status"] = "refuted"
        with self.assertRaisesRegex(ContractError, "counterevidence"):
            validate_findings(value)

    def test_refuted_finding_and_verification_statuses_match(self) -> None:
        value = finding_document()
        value["findings"][0]["status"] = "refuted"
        with self.assertRaisesRegex(ContractError, "statuses must match"):
            validate_findings(value)

    def test_downgrade_requires_lower_resulting_severity(self) -> None:
        value = finding_document("high")
        finding = value["findings"][0]
        finding["verification"].update(
            status="downgraded",
            counterevidence=[{"path": "src/module", "detail": "Impact is locally contained."}],
        )
        with self.assertRaisesRegex(ContractError, "lower resulting severity"):
            validate_findings(value)
        finding["verification"]["resulting_severity"] = "medium"
        validate_findings(value)

    def test_scope_rejects_globs_and_accepts_limitations(self) -> None:
        value = finding_document()
        value["scope"]["included"] = ["packages/*/package.json"]
        with self.assertRaisesRegex(ContractError, r"glob characters.*packages/\*/package\.json"):
            validate_findings(value)
        value = finding_document()
        value["scope"]["limitations"] = ["The index parser does not cover Bash; those files were read directly."]
        validate_findings(value)
        value["scope"]["limitations"] = [""]
        with self.assertRaisesRegex(ContractError, "limitations"):
            validate_findings(value)

    def test_strip_code_spans_hides_quoted_wikilinks(self) -> None:
        text = "Real [[context]] link.\nQuoted `[[decisions#ADR-025|ADR-025]]` example.\n```\n[[fenced#Anchor]]\n```\n"
        stripped = strip_code_spans(text)
        self.assertIn("[[context]]", stripped)
        self.assertNotIn("ADR-025", stripped)
        self.assertNotIn("fenced", stripped)
        self.assertNotIn("double", strip_code_spans("Quoted ``[[double#Anchor]]`` example."))
        self.assertNotIn("tilde", strip_code_spans("~~~\n[[tilde#Anchor]]\n~~~\n"))
        mixed = strip_code_spans("```\ncode\n   ```\nreal [[context]] text\n```\nmore code\n```\n")
        self.assertIn("[[context]]", mixed)

    def test_config_excludes_are_literal_paths(self) -> None:
        for bad in ("packages/*", ":!keep", "app/[locale]"):
            value = config()
            value["audit"]["exclude"] = [bad]
            with self.assertRaisesRegex(ContractError, "glob or pathspec-magic"):
                validate_config(value)

    def test_resulting_severity_must_match_unless_downgraded(self) -> None:
        value = finding_document()
        value["findings"][0]["verification"]["resulting_severity"] = "critical"
        with self.assertRaisesRegex(ContractError, "resulting_severity must match"):
            validate_findings(value)

    def test_refuted_finding_cannot_be_resurrected(self) -> None:
        previous = finding_document("high")
        previous["findings"][0]["status"] = "refuted"
        previous["findings"][0]["verification"].update(
            status="refuted",
            resulting_severity=None,
            counterevidence=[{"path": "src/module", "detail": "The claim does not reproduce."}],
        )
        with self.assertRaisesRegex(ContractError, "refuted finding cannot return as active"):
            validate_findings(finding_document("high"), previous)

    def test_previous_findings_must_use_same_auditor(self) -> None:
        previous = finding_document()
        previous["auditor"] = "stack"
        previous["findings"][0]["id"] = "stack-001"
        with self.assertRaisesRegex(ContractError, "different auditor"):
            validate_findings(finding_document(), previous)

    def test_inventory_is_append_only(self) -> None:
        previous = inventory()
        current = copy.deepcopy(previous)
        second = copy.deepcopy(current["runs"][0])
        second["id"] = "run-20260802-2"
        current["runs"].append(second)
        validate_inventory(current, previous)
        current["runs"][0]["tools"]["jcodemunch"] = "skipped"
        with self.assertRaisesRegex(ContractError, "append-only"):
            validate_inventory(current, previous)

    def test_inventory_derives_outcome_from_coverage(self) -> None:
        value = inventory()
        value["runs"][0]["scope"]["unscanned"] = ["vendor/blob"]
        with self.assertRaisesRegex(ContractError, "coverage-incomplete"):
            validate_inventory(value)
        value["runs"][0]["outcome"] = "coverage-incomplete"
        validate_inventory(value)

    def test_inventory_requires_full_domain_coverage(self) -> None:
        value = inventory()
        value["runs"][0]["domains"]["ui"] = "enabled"
        with self.assertRaisesRegex(ContractError, "required"):
            validate_inventory(value)
        value["runs"][0]["coverage"]["required"].append("ui")
        value["runs"][0]["coverage"]["skipped"].pop("ui")
        value["runs"][0]["coverage"]["completed"].append("ui")
        validate_inventory(value)

    def test_inventory_blind_verification_controls_outcome(self) -> None:
        value = inventory()
        value["runs"][0]["verification"]["blind"] = "not-run"
        with self.assertRaisesRegex(ContractError, "coverage-incomplete"):
            validate_inventory(value)
        value["runs"][0]["outcome"] = "coverage-incomplete"
        validate_inventory(value)
        value["runs"][0]["verification"] = {"blind": "failed", "issues": 0}
        with self.assertRaisesRegex(ContractError, "at least one issue"):
            validate_inventory(value)
        value["runs"][0]["verification"]["issues"] = 1
        value["runs"][0]["outcome"] = "failed"
        validate_inventory(value)

    def test_project_map_rejects_structural_errors(self) -> None:
        duplicate = project_map()
        duplicate["nodes"].append(copy.deepcopy(duplicate["nodes"][0]))
        with self.assertRaisesRegex(ContractError, "unique lowercase identifier"):
            validate_project_map(duplicate)
        unknown_edge = project_map()
        unknown_edge["edges"] = [
            {
                "from": "module",
                "to": "missing",
                "label": "calls",
                "evidence": [{"path": "src/module", "detail": "Call site."}],
            }
        ]
        with self.assertRaisesRegex(ContractError, "unknown node"):
            validate_project_map(unknown_edge)
        no_evidence = project_map()
        no_evidence["nodes"][0]["evidence"] = []
        with self.assertRaisesRegex(ContractError, "must not be empty"):
            validate_project_map(no_evidence)

    def test_manifest_template_and_fixture_are_valid(self) -> None:
        template = strict_json_loads((ROOT / "templates/project-context.manifest.json").read_text())
        validate_manifest(template)
        template["artifacts"].append(copy.deepcopy(template["artifacts"][0]))
        template["artifacts"][-1]["id"] = "context_copy"
        with self.assertRaisesRegex(ContractError, "duplicate manifest artifact path"):
            validate_manifest(template)

    def test_manifest_rejects_paths_outside_generated_surface(self) -> None:
        value = strict_json_loads((ROOT / "templates/project-context.manifest.json").read_text())
        value["artifacts"][0]["path"] = "src/main.py"
        with self.assertRaisesRegex(ContractError, "outside the generated surface"):
            validate_manifest(value)

    def test_context_id_must_name_root_context(self) -> None:
        value = strict_json_loads((ROOT / "templates/project-context.manifest.json").read_text())
        value["artifacts"][0]["id"] = "root"
        with self.assertRaisesRegex(ContractError, "context id"):
            validate_manifest(value)


class HostMergeTests(unittest.TestCase):
    def test_insert_is_idempotent(self) -> None:
        original = "# Local rules\nKeep this byte-for-byte.\n"
        merged = merge_host_text(original, "claude")
        self.assertEqual(merged, merge_host_text(merged, "claude"))
        block = extract_host_block(merged, "claude")
        self.assertIsNotNone(block)
        assert block is not None
        start = merged.index(block)
        self.assertEqual(0, start)
        self.assertTrue(merged.endswith(original))

    def test_large_codex_file_still_starts_with_managed_block(self) -> None:
        original = "x" * 40_000
        merged = merge_host_text(original, "codex")
        self.assertTrue(merged.startswith("<!-- project-context:codex:begin v1 -->"))
        self.assertTrue(merged.endswith(original))

    def test_replace_preserves_prefix_and_suffix(self) -> None:
        current = merge_host_text("prefix\r\n", "codex") + "suffix-without-newline"
        block = extract_host_block(current, "codex")
        assert block is not None
        prefix, suffix = current.split(block)
        replaced = merge_host_text(current, "codex")
        new_block = extract_host_block(replaced, "codex")
        assert new_block is not None
        self.assertEqual(prefix, replaced[: replaced.index(new_block)])
        self.assertEqual(suffix, replaced[replaced.index(new_block) + len(new_block) :])

    def test_rejects_partial_or_duplicate_markers(self) -> None:
        begin = "<!-- project-context:claude:begin v1 -->"
        with self.assertRaisesRegex(ContractError, "exactly once"):
            merge_host_text(begin, "claude")
        valid = merge_host_text("", "claude")
        with self.assertRaisesRegex(ContractError, "exactly once"):
            merge_host_text(valid + valid, "claude")

    def test_rejects_out_of_order_markers(self) -> None:
        begin, end = HOST_MARKERS["claude"]
        with self.assertRaisesRegex(ContractError, "out of order"):
            extract_host_block(f"{end}\nuser text\n{begin}\n", "claude")

    def test_expected_hash_is_an_optimistic_lock(self) -> None:
        text = "user content\n"
        merge_host_text(text, "claude", sha256_text(text))
        with self.assertRaisesRegex(ContractError, "changed since preview"):
            merge_host_text(text + "changed", "claude", sha256_text(text))


class ProjectValidationTests(unittest.TestCase):
    @staticmethod
    def _write_project(
        root: Path,
        revision: str | None = AUDIT_REVISION,
        worktree_clean: bool | None = True,
    ) -> dict[str, Any]:
        (root / "repodocs/audit").mkdir(parents=True)
        config_value = config()
        config_raw = (json.dumps(config_value, indent=2, sort_keys=True) + "\n").encode()
        (root / "repodocs/project-context.config.json").write_bytes(config_raw)

        inventory_value = inventory(revision, worktree_clean)
        inventory_value["runs"][0]["outcome"] = "coverage-incomplete"
        inventory_value["runs"][0]["coverage"]["completed"] = []
        inventory_raw = (json.dumps(inventory_value, indent=2, sort_keys=True) + "\n").encode()
        (root / "repodocs/audit/inventory.json").write_bytes(inventory_raw)

        project_map_raw = (
            json.dumps(project_map(inventory_value["runs"][0]["id"]), indent=2, sort_keys=True) + "\n"
        ).encode()
        (root / "repodocs/project-map.json").write_bytes(project_map_raw)

        file_artifacts = [
            ("architecture", "repodocs/architecture.md"),
            ("techstack", "repodocs/techstack.md"),
            ("security", "repodocs/security.md"),
            ("testing", "repodocs/testing.md"),
            ("edge_cases", "repodocs/edge-cases.md"),
            ("decisions", "repodocs/decisions.md"),
            ("legacy_warning", "repodocs/LegacyWarning.md"),
            ("migration_backlog", "repodocs/migration-backlog.md"),
            ("drift_report", "repodocs/audit/drift-report.md"),
        ]
        files = {"PROJECT_CONTEXT.md": "# Context\n\nSee [[architecture]] and [[context]].\n"}
        files.update({path: f"# {artifact_id}\n" for artifact_id, path in file_artifacts})
        for relative, text in files.items():
            (root / relative).write_text(text, encoding="utf-8")
        claude = merge_host_text("# Claude-only note\n", "claude")
        codex = merge_host_text("# Codex-only note\n", "codex")
        (root / "CLAUDE.md").write_text(claude, encoding="utf-8")
        (root / "AGENTS.md").write_text(codex, encoding="utf-8")

        claude_block = extract_host_block(claude, "claude")
        codex_block = extract_host_block(codex, "codex")
        assert claude_block is not None and codex_block is not None
        manifest = {
            "schema_version": 1,
            "skill_version": SKILL_VERSION,
            "config_sha256": sha256_bytes(config_raw),
            "domains": ["architecture", "stack", "security", "testing"],
            "hosts": ["claude", "codex"],
            "artifacts": [
                {
                    "id": "context",
                    "path": "PROJECT_CONTEXT.md",
                    "kind": "owned_file",
                    "sha256": sha256_bytes(files["PROJECT_CONTEXT.md"].encode()),
                },
                *(
                    {
                        "id": artifact_id,
                        "path": path,
                        "kind": "owned_file",
                        "sha256": sha256_text(files[path]),
                    }
                    for artifact_id, path in file_artifacts
                ),
                {
                    "id": "project_map",
                    "path": "repodocs/project-map.json",
                    "kind": "owned_file",
                    "sha256": sha256_bytes(project_map_raw),
                },
                {
                    "id": "audit_inventory",
                    "path": "repodocs/audit/inventory.json",
                    "kind": "owned_file",
                    "sha256": sha256_bytes(inventory_raw),
                },
                {
                    "id": "claude_host",
                    "path": "CLAUDE.md",
                    "kind": "managed_block",
                    "sha256": sha256_text(claude_block),
                },
                {
                    "id": "codex_host",
                    "path": "AGENTS.md",
                    "kind": "managed_block",
                    "sha256": sha256_text(codex_block),
                },
            ],
        }
        (root / "repodocs/project-context.manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return manifest

    @staticmethod
    def _add_architecture_findings(
        root: Path,
        manifest: dict[str, Any],
        *,
        run_id: str = RUN_ID,
        reference: str = "architecture-001",
        title: str | None = None,
    ) -> None:
        inventory_path = root / "repodocs/audit/inventory.json"
        inventory_value = strict_json_loads(inventory_path.read_text())
        inventory_value["runs"][0]["coverage"]["completed"] = ["architecture"]
        inventory_raw = json.dumps(inventory_value, sort_keys=True).encode()
        inventory_path.write_bytes(inventory_raw)
        next(item for item in manifest["artifacts"] if item["id"] == "audit_inventory")["sha256"] = sha256_bytes(
            inventory_raw
        )

        findings_value = finding_document(run_id=run_id)
        if title is not None:
            findings_value["findings"][0]["title"] = title
        findings_raw = json.dumps(findings_value, sort_keys=True).encode()
        findings_path = root / "repodocs/audit/findings/architecture.json"
        findings_path.parent.mkdir(parents=True, exist_ok=True)
        findings_path.write_bytes(findings_raw)
        finding_artifact = next(
            (item for item in manifest["artifacts"] if item["id"] == "finding_architecture"),
            None,
        )
        if finding_artifact is None:
            finding_artifact = {
                "id": "finding_architecture",
                "path": "repodocs/audit/findings/architecture.json",
                "kind": "owned_file",
                "sha256": "",
            }
            manifest["artifacts"].append(finding_artifact)
        finding_artifact["sha256"] = sha256_bytes(findings_raw)

        decisions = f"# decisions\n\n- Sources: {reference}\n"
        (root / "repodocs/decisions.md").write_text(decisions, encoding="utf-8")
        next(item for item in manifest["artifacts"] if item["id"] == "decisions")["sha256"] = sha256_text(decisions)
        (root / "repodocs/project-context.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_warns_on_unmanaged_repodocs_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write_project(root)
            self.assertEqual([], validate_project(root)["warnings"])
            (root / "repodocs/notes.md").write_text("stray hand-written notes\n", encoding="utf-8")
            self.assertIn(
                "unmanaged file in repodocs/: repodocs/notes.md (not owned by the manifest)",
                validate_project(root)["warnings"],
            )

    def test_detects_host_block_drift_and_absence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write_project(root)
            claude_path = root / "CLAUDE.md"
            original = claude_path.read_text(encoding="utf-8")
            block = extract_host_block(original, "claude")
            assert block is not None
            claude_path.write_text(
                original.replace(block, block.replace("PROJECT_CONTEXT.md", "OTHER.md")), encoding="utf-8"
            )
            with self.assertRaisesRegex(ContractError, "drifted"):
                validate_project(root)
            claude_path.write_text("# No managed block here\n", encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "managed block is missing"):
                validate_project(root)

    def test_detects_manifest_hosts_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_project(root)
            manifest["hosts"] = ["claude"]
            (root / "repodocs/project-context.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "hosts do not match"):
                validate_project(root)

    def test_valid_project_and_user_host_edits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write_project(root)
            self.assertEqual("valid", validate_project(root)["status"])
            path = root / "CLAUDE.md"
            path.write_text("new user prefix\n" + path.read_text(), encoding="utf-8")
            self.assertEqual("valid", validate_project(root)["status"])

    def test_detects_artifact_hash_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write_project(root)
            (root / "repodocs/architecture.md").write_text("changed\n", encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "hash mismatch"):
                validate_project(root)

    def test_detects_unknown_root_wikilink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_project(root)
            text = "# Context\n\nSee [[missing]].\n"
            (root / "PROJECT_CONTEXT.md").write_text(text, encoding="utf-8")
            manifest["artifacts"][0]["sha256"] = sha256_text(text)
            (root / "repodocs/project-context.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "unknown wikilinks"):
                validate_project(root)

    def test_detects_unknown_wikilink_in_supporting_doc(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_project(root)
            text = "# Architecture\n\nSee [[missing]].\n"
            (root / "repodocs/architecture.md").write_text(text, encoding="utf-8")
            manifest["artifacts"][1]["sha256"] = sha256_text(text)
            (root / "repodocs/project-context.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "unknown wikilinks"):
                validate_project(root)

    def test_detects_malformed_wikilink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_project(root)
            text = "# Context\n\nSee [[Missing]].\n"
            (root / "PROJECT_CONTEXT.md").write_text(text, encoding="utf-8")
            manifest["artifacts"][0]["sha256"] = sha256_text(text)
            (root / "repodocs/project-context.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "malformed wikilinks"):
                validate_project(root)

    @requires_symlinks
    def test_detects_symlink_artifact_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write_project(root)
            target = root / "outside.md"
            target.write_text("outside\n", encoding="utf-8")
            artifact = root / "repodocs/architecture.md"
            artifact.unlink()
            artifact.symlink_to(target)
            with self.assertRaisesRegex(ContractError, "symlink"):
                validate_project(root)

    def test_full_layout_requires_resolved_conditional_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_project(root)
            config_path = root / "repodocs/project-context.config.json"
            config_value = config()
            config_value["domains"]["ui"] = "enabled"
            config_raw = (json.dumps(config_value, indent=2, sort_keys=True) + "\n").encode()
            config_path.write_bytes(config_raw)
            manifest["config_sha256"] = sha256_bytes(config_raw)
            manifest["domains"].append("ui")
            inventory_path = root / "repodocs/audit/inventory.json"
            inventory_value = strict_json_loads(inventory_path.read_text())
            inventory_value["runs"][0]["domains"]["ui"] = "enabled"
            inventory_value["runs"][0]["coverage"]["required"].append("ui")
            inventory_raw = (json.dumps(inventory_value, indent=2, sort_keys=True) + "\n").encode()
            inventory_path.write_bytes(inventory_raw)
            next(item for item in manifest["artifacts"] if item["id"] == "audit_inventory")["sha256"] = sha256_bytes(
                inventory_raw
            )
            manifest_path = root / "repodocs/project-context.manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "requires repodocs/ui-kit.md"):
                validate_project(root)

    def test_compact_layout_requires_canonical_topic_links_and_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_project(root)
            config_path = root / "repodocs/project-context.config.json"
            config_value = config()
            config_value["document_layout"] = "compact"
            config_raw = (json.dumps(config_value, indent=2, sort_keys=True) + "\n").encode()
            config_path.write_bytes(config_raw)
            manifest["config_sha256"] = sha256_bytes(config_raw)
            topic_ids = {"architecture", "techstack", "security", "testing", "edge_cases"}
            for artifact in list(manifest["artifacts"]):
                if artifact["id"] in topic_ids:
                    (root / artifact["path"]).unlink()
                    manifest["artifacts"].remove(artifact)
            topics = ("stack", "architecture", "security", "testing", "edge-cases")
            context = "# Context\n\n" + "\n".join(
                f'[[context#{topic}]]\n\n<a id="{topic}"></a>\n## {topic}' for topic in topics
            ) + "\n"
            (root / "PROJECT_CONTEXT.md").write_text(context, encoding="utf-8")
            next(item for item in manifest["artifacts"] if item["id"] == "context")["sha256"] = sha256_text(context)
            manifest_path = root / "repodocs/project-context.manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            validate_project(root)
            context = context.replace('<a id="testing"></a>\n', "")
            (root / "PROJECT_CONTEXT.md").write_text(context, encoding="utf-8")
            next(item for item in manifest["artifacts"] if item["id"] == "context")["sha256"] = sha256_text(context)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "unresolved wikilink anchor: context#testing"):
                validate_project(root)

    def test_rejects_unknown_wikilink_fragment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_project(root)
            text = "# Context\n\nSee [[decisions#missing]].\n"
            (root / "PROJECT_CONTEXT.md").write_text(text, encoding="utf-8")
            next(item for item in manifest["artifacts"] if item["id"] == "context")["sha256"] = sha256_text(text)
            (root / "repodocs/project-context.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "unresolved wikilink anchor: decisions#missing"):
                validate_project(root)

    def test_completed_auditor_requires_manifest_owned_findings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_project(root)
            inventory_path = root / "repodocs/audit/inventory.json"
            inventory_value = strict_json_loads(inventory_path.read_text())
            inventory_value["runs"][0]["coverage"]["completed"] = ["architecture"]
            inventory_raw = json.dumps(inventory_value).encode()
            inventory_path.write_bytes(inventory_raw)
            next(item for item in manifest["artifacts"] if item["id"] == "audit_inventory")["sha256"] = sha256_bytes(
                inventory_raw
            )
            (root / "repodocs/project-context.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "missing completed auditor findings"):
                validate_project(root)

    def test_rejects_stale_findings_and_project_map_run_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_project(root)
            self._add_architecture_findings(root, manifest, run_id="stale-run")
            with self.assertRaisesRegex(ContractError, "findings run_id does not match"):
                validate_project(root)

            self._add_architecture_findings(root, manifest)
            map_path = root / "repodocs/project-map.json"
            map_value = strict_json_loads(map_path.read_text())
            map_value["run_id"] = "stale-run"
            map_raw = json.dumps(map_value).encode()
            map_path.write_bytes(map_raw)
            next(item for item in manifest["artifacts"] if item["id"] == "project_map")["sha256"] = sha256_bytes(
                map_raw
            )
            (root / "repodocs/project-context.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "project map run_id does not match"):
                validate_project(root)

    def test_rejects_inventory_source_state_that_disagrees_with_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_project(root)
            inventory_path = root / "repodocs/audit/inventory.json"
            value = strict_json_loads(inventory_path.read_text())
            value["runs"][0]["source_state"] = "greenfield"
            value["runs"][0]["coverage"]["required"] = ["greenfield"]
            raw = json.dumps(value).encode()
            inventory_path.write_bytes(raw)
            next(item for item in manifest["artifacts"] if item["id"] == "audit_inventory")["sha256"] = sha256_bytes(raw)
            (root / "repodocs/project-context.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "source_state does not match"):
                validate_project(root)

    def test_rejects_active_finding_outside_completed_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_project(root)
            self._add_architecture_findings(root, manifest)
            inventory_path = root / "repodocs/audit/inventory.json"
            inventory_value = strict_json_loads(inventory_path.read_text())
            inventory_value["runs"][0]["scope"]["unscanned"] = ["src"]
            inventory_raw = json.dumps(inventory_value).encode()
            inventory_path.write_bytes(inventory_raw)
            next(item for item in manifest["artifacts"] if item["id"] == "audit_inventory")["sha256"] = sha256_bytes(
                inventory_raw
            )
            findings_path = root / "repodocs/audit/findings/architecture.json"
            findings_value = strict_json_loads(findings_path.read_text())
            findings_value["scope"]["unscanned"] = ["src"]
            findings_raw = json.dumps(findings_value).encode()
            findings_path.write_bytes(findings_raw)
            next(item for item in manifest["artifacts"] if item["id"] == "finding_architecture")["sha256"] = sha256_bytes(
                findings_raw
            )
            map_path = root / "repodocs/project-map.json"
            map_value = project_map()
            map_value["nodes"] = []
            map_raw = json.dumps(map_value).encode()
            map_path.write_bytes(map_raw)
            next(item for item in manifest["artifacts"] if item["id"] == "project_map")["sha256"] = sha256_bytes(map_raw)
            (root / "repodocs/project-context.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "active finding is outside completed audit scope"):
                validate_project(root)

    def test_planned_project_map_edge_requires_adr_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_project(root)
            map_path = root / "repodocs/project-map.json"
            value = project_map()
            value["nodes"].append(
                {
                    "id": "target",
                    "label": "Target",
                    "kind": "component",
                    "status": "planned",
                    "evidence": [{"path": "repodocs/decisions.md", "detail": "Accepted target."}],
                }
            )
            value["edges"] = [
                {
                    "from": "module",
                    "to": "target",
                    "label": "migrates to",
                    "evidence": [{"path": "src/module", "detail": "Current source."}],
                }
            ]
            raw = json.dumps(value).encode()
            map_path.write_bytes(raw)
            next(item for item in manifest["artifacts"] if item["id"] == "project_map")["sha256"] = sha256_bytes(raw)
            (root / "repodocs/project-context.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "planned project-map edge needs ADR evidence"):
                validate_project(root)

    def test_active_finding_reference_requires_exact_id_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_project(root)
            self._add_architecture_findings(root, manifest, reference="prefix-architecture-001-suffix")
            with self.assertRaisesRegex(ContractError, "active finding lacks a disposition reference"):
                validate_project(root)
            self._add_architecture_findings(root, manifest, reference="Disposition: architecture-001.")
            validate_project(root)

    def test_active_finding_reference_must_be_visible_on_sources_line(self) -> None:
        hidden_references = {
            "tilde fence": "placeholder\n\n~~~\n- Sources: architecture-001\n~~~",
            "indented code": "placeholder\n\n    - Sources: architecture-001",
            "link destination": "[context](architecture-001)",
            "html attribute": '<span data-id="architecture-001">context</span>',
            "quoted html delimiter": '<span title="> architecture-001">context</span>',
        }
        for label, reference in hidden_references.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                manifest = self._write_project(root)
                self._add_architecture_findings(root, manifest, reference=reference)
                with self.assertRaisesRegex(ContractError, "active finding lacks a disposition reference"):
                    validate_project(root)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_project(root)
            self._add_architecture_findings(root, manifest, reference="[architecture-001](#finding)")
            validate_project(root)

    def test_normalizes_text_line_endings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write_project(root)
            for relative in (
                "PROJECT_CONTEXT.md",
                "CLAUDE.md",
                "AGENTS.md",
                "repodocs/architecture.md",
                "repodocs/audit/inventory.json",
                "repodocs/project-context.config.json",
            ):
                path = root / relative
                path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
            validate_project(root)

    def test_rejects_stale_skill_version_and_unapproved_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_project(root)
            manifest_path = root / "repodocs/project-context.manifest.json"
            manifest["skill_version"] = "0.1.0"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "contract versions differ"):
                validate_project(root)
            major, minor, patch = SKILL_VERSION.split(".")
            manifest["skill_version"] = f"{major}.{minor}.{int(patch) + 1}"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            summary = validate_project(root)  # a patch delta warns, never invalidates
            self.assertEqual("valid", summary["status"])
            self.assertTrue(any("compatible patch" in warning for warning in summary["warnings"]))
            manifest["skill_version"] = SKILL_VERSION
            inventory_path = root / "repodocs/audit/inventory.json"
            inventory_value = strict_json_loads(inventory_path.read_text())
            inventory_value["runs"][0]["scope"]["excluded"] = ["vendor"]
            inventory_raw = json.dumps(inventory_value).encode()
            inventory_path.write_bytes(inventory_raw)
            next(item for item in manifest["artifacts"] if item["id"] == "audit_inventory")["sha256"] = sha256_bytes(
                inventory_raw
            )
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "unapproved audit exclusions"):
                validate_project(root)


class PreflightAndSelfCheckTests(unittest.TestCase):
    def test_preflight_disables_repository_fsmonitor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            marker = root / "fsmonitor-ran"
            hook = root / "fsmonitor-hook"
            hook.write_text(f"#!/bin/sh\ntouch {marker!s}\nexit 1\n", encoding="utf-8")
            hook.chmod(0o755)
            subprocess.run(["git", "-C", str(root), "config", "core.fsmonitor", str(hook)], check=True)
            preflight(root)
            self.assertFalse(marker.exists())

    def test_preflight_reports_absent_valid_and_invalid_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            self.assertEqual("absent", preflight(root)["context_state"])
            (root / ".jcodemunch.jsonc").write_text("{}\n", encoding="utf-8")
            self.assertEqual("absent", preflight(root)["context_state"])
            ProjectValidationTests._write_project(root)
            (root / "CLAUDE.md").write_text(merge_host_text("", "claude"), encoding="utf-8")
            (root / "AGENTS.md").write_text(merge_host_text("", "codex"), encoding="utf-8")
            self.assertEqual("valid", preflight(root)["context_state"])
            (root / "repodocs/architecture.md").write_text("drifted\n", encoding="utf-8")
            result = preflight(root)
            self.assertEqual("invalid", result["context_state"])
            self.assertIn("hash mismatch", result["context_error"])

    def test_preflight_is_technology_neutral(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "README.md").write_text("\n", encoding="utf-8")
            self.assertEqual("greenfield", preflight(root)["source_state"])
            (root / "project.specification").write_text("declarative state\n", encoding="utf-8")
            result = preflight(root)
            self.assertEqual("codebase", result["source_state"])
            self.assertIn("project.specification", result["source_evidence"])

    def test_preflight_treats_vcs_metadata_as_scaffolding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / ".gitignore").write_text(".DS_Store\n", encoding="utf-8")
            (root / ".gitattributes").write_text("* text=auto\n", encoding="utf-8")
            self.assertEqual("greenfield", preflight(root)["source_state"])

    def test_preflight_distinguishes_generated_and_handwritten_repodocs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "LICENSE").write_text("A real license is valid greenfield scaffolding.\n", encoding="utf-8")
            (root / "repodocs").mkdir()
            (root / "repodocs/architecture.md").write_text("unclaimed\n", encoding="utf-8")
            self.assertEqual("codebase", preflight(root)["source_state"])

    def test_preflight_ignores_only_manifest_verified_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            manifest = ProjectValidationTests._write_project(root)
            (root / "CLAUDE.md").write_text(merge_host_text("", "claude"), encoding="utf-8")
            (root / "AGENTS.md").write_text(merge_host_text("", "codex"), encoding="utf-8")
            inventory_path = root / "repodocs/audit/inventory.json"
            inventory_value = strict_json_loads(inventory_path.read_text())
            inventory_value["runs"][0]["source_state"] = "greenfield"
            inventory_value["runs"][0]["coverage"]["required"] = ["greenfield"]
            inventory_raw = json.dumps(inventory_value).encode()
            inventory_path.write_bytes(inventory_raw)
            next(item for item in manifest["artifacts"] if item["id"] == "audit_inventory")["sha256"] = sha256_bytes(
                inventory_raw
            )
            map_path = root / "repodocs/project-map.json"
            map_value = project_map()
            map_value["nodes"] = []
            map_raw = json.dumps(map_value).encode()
            map_path.write_bytes(map_raw)
            next(item for item in manifest["artifacts"] if item["id"] == "project_map")["sha256"] = sha256_bytes(map_raw)
            (root / "repodocs/project-context.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual("greenfield", preflight(root)["source_state"])
            (root / "repodocs/architecture.md").write_text("drifted\n", encoding="utf-8")
            self.assertEqual("codebase", preflight(root)["source_state"])

    @requires_symlinks
    def test_preflight_counts_nonignored_symlink_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            outside = root.parent / f"{root.name}-tree"
            outside.mkdir()
            try:
                (root / "linked").symlink_to(outside, target_is_directory=True)
                result = preflight(root)
                self.assertEqual("codebase", result["source_state"])
                self.assertIn("linked", result["source_evidence"])
            finally:
                outside.rmdir()

    @requires_symlinks
    def test_preflight_does_not_follow_symlinked_readme(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            outside = root.parent / f"{root.name}-readme"
            outside.write_text("", encoding="utf-8")
            try:
                (root / "README.md").symlink_to(outside)
                result = preflight(root)
                self.assertEqual("codebase", result["source_state"])
                self.assertIn("README.md", result["source_evidence"])
            finally:
                outside.unlink()

    @requires_symlinks
    def test_preflight_rejects_symlink_under_repodocs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "repodocs").mkdir()
            outside = root.parent / f"{root.name}-target"
            outside.write_text("", encoding="utf-8")
            try:
                (root / "repodocs/linked.md").symlink_to(outside)
                result = preflight(root)  # reported structurally, never a crash
                self.assertTrue(
                    any("symlinks are not allowed" in entry["error"] for entry in result["host_errors"])
                )
            finally:
                outside.unlink()

    @requires_symlinks
    def test_preflight_reports_broken_host_file_structurally(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "AGENTS.md").write_text("shared instructions\n", encoding="utf-8")
            (root / "CLAUDE.md").symlink_to(root / "AGENTS.md")
            result = preflight(root)  # symlinked host files are widespread practice
            self.assertTrue(any(entry["path"] == "CLAUDE.md" for entry in result["host_errors"]))
            self.assertIn(result["context_state"], {"absent", "invalid"})
            snapshot = dashboard_snapshot(root)  # and the dashboard must still start
            self.assertIn(snapshot["context"]["state"], {"absent", "invalid"})

    def test_preflight_reports_v01_legacy_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "agents.md").write_text("v0.1 output\n", encoding="utf-8")
            (root / "repodocs/audit/findings").mkdir(parents=True)
            (root / "repodocs/audit/inventory.yaml").write_text("scanned_at: 2026-07-13\n", encoding="utf-8")
            (root / "repodocs/audit/findings/stack.yaml").write_text("auditor: stack\n", encoding="utf-8")
            legacy = preflight(root)["legacy_surfaces"]
            for expected in ("agents.md", "repodocs/audit/inventory.yaml", "repodocs/audit/findings/stack.yaml"):
                self.assertIn(expected, legacy)

    def test_preflight_legacy_agents_check_is_case_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "AGENTS.md").write_text(merge_host_text("", "codex"), encoding="utf-8")
            self.assertNotIn("agents.md", preflight(root)["legacy_surfaces"])

    def test_preflight_scope_review_cross_checks_git(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "repodocs").mkdir()
            config_value = config()
            config_value["audit"]["exclude"] = ["skip-zone"]
            (root / "repodocs/project-context.config.json").write_text(json.dumps(config_value), encoding="utf-8")
            (root / "skip-zone/prototype").mkdir(parents=True)
            (root / "skip-zone/prototype/app.ts").write_text("export {}\n", encoding="utf-8")
            (root / "skip-zone/prototype/AGENTS.md").write_text("instructions\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "skip-zone"], check=True)
            (root / ".gitignore").write_text("skip-zone/\n", encoding="utf-8")
            review = preflight(root)["scope_review"]
            self.assertEqual(1, len(review))
            entry = review[0]
            self.assertEqual("skip-zone", entry["path"])
            self.assertEqual(2, entry["tracked_files"])
            self.assertTrue(entry["tracked_and_ignored"])
            self.assertEqual(["skip-zone/prototype/AGENTS.md"], entry["agent_instruction_files"])

    def test_preflight_reports_existing_decision_citations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "notes.ts").write_text("// per ADR-031 and MB-022\n// see ADR-031 again\n// heap sized 512MB-4GB and a LOADR-9 register are not decision ids\n", encoding="utf-8")
            (root / "repodocs").mkdir()
            (root / "repodocs/decisions.md").write_text("ADR-099 must not be reported\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
            citations = preflight(root)["decision_citations"]
            self.assertEqual({"ADR-031", "MB-022"}, {entry["id"] for entry in citations})
            adr = next(entry for entry in citations if entry["id"] == "ADR-031")
            self.assertEqual(["notes.ts"], adr["files"])

    def test_preflight_maps_agent_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "AGENTS.md").write_text("root instructions\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "AGENTS.md"], check=True)
            (root / ".cursor/rules").mkdir(parents=True)
            (root / ".cursor/rules/style.mdc").write_text("rule\n", encoding="utf-8")
            (root / ".agents/skills/demo/reference").mkdir(parents=True)
            (root / ".agents/skills/demo/SKILL.md").write_text("skill\n", encoding="utf-8")
            (root / ".agents/skills/demo/reference/deep.md").write_text("internal\n", encoding="utf-8")
            (root / "CLAUDE.md").write_text("ignored root host file\n", encoding="utf-8")
            (root / ".gitignore").write_text("/CLAUDE.md\n", encoding="utf-8")
            entries = {entry["path"]: entry for entry in preflight(root)["agent_instructions"]}
            self.assertIn("AGENTS.md", entries)
            self.assertTrue(entries["AGENTS.md"]["tracked"])
            self.assertIn("CLAUDE.md", entries)
            self.assertFalse(entries["CLAUDE.md"]["tracked"])
            self.assertIn(".cursor/rules/style.mdc", entries)
            self.assertFalse(entries[".cursor/rules/style.mdc"]["tracked"])
            self.assertIn(".agents/skills/demo/SKILL.md", entries)
            self.assertNotIn(".agents/skills/demo/reference/deep.md", entries)
            self.assertIn("modified", entries["AGENTS.md"])

    def test_instruction_map_sees_git_ignored_instruction_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "apps/web").mkdir(parents=True)
            (root / "apps/web/CLAUDE.md").write_text("nested, ignored, still loaded by the host\n", encoding="utf-8")
            (root / ".gitignore").write_text("apps/*/CLAUDE.md\n", encoding="utf-8")
            entries = {entry["path"]: entry for entry in preflight(root)["agent_instructions"]}
            self.assertIn("apps/web/CLAUDE.md", entries)
            self.assertTrue(entries["apps/web/CLAUDE.md"]["ignored"])
            self.assertFalse(entries["apps/web/CLAUDE.md"]["tracked"])

    def test_governance_anchor_requires_matching_heading(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._project_with_decisions(root, '# decisions\n\n<a id="ADR-001"></a>\n## Решение ADR-001\n')
            with self.assertRaisesRegex(ContractError, "no matching '## ADR-001:"):
                validate_project(root)
            self._project_with_decisions(root, '# decisions\n\n<a id="ADR-001"></a>\n## ADR-001: Решение\n', fresh=False)
            self.assertEqual("valid", validate_project(root)["status"])

    @staticmethod
    def _project_with_decisions(root: Path, decisions_text: str, fresh: bool = True) -> dict[str, Any]:
        if fresh:
            manifest = ProjectValidationTests._write_project(root)
        decisions_path = root / "repodocs/decisions.md"
        decisions_path.write_text(decisions_text, encoding="utf-8")
        manifest_path = root / "repodocs/project-context.manifest.json"
        manifest = strict_json_loads(manifest_path.read_text())
        for artifact in manifest["artifacts"]:
            if artifact["path"] == "repodocs/decisions.md":
                artifact["sha256"] = sha256_text(decisions_text)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    def test_skill_directory_digest_sees_reference_divergence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            for host in (".agents", ".claude"):
                (root / host / "skills/demo/reference").mkdir(parents=True)
                (root / host / "skills/demo/SKILL.md").write_text("same skill body\n", encoding="utf-8")
                (root / host / "skills/demo/reference/deep.md").write_text("shared\n", encoding="utf-8")
            entries = {entry["path"]: entry["sha256"] for entry in preflight(root)["agent_instructions"]}
            self.assertEqual(entries[".agents/skills/demo/SKILL.md"], entries[".claude/skills/demo/SKILL.md"])
            (root / ".agents/skills/demo/reference/deep.md").write_text("diverged\n", encoding="utf-8")
            entries = {entry["path"]: entry["sha256"] for entry in preflight(root)["agent_instructions"]}
            self.assertNotEqual(entries[".agents/skills/demo/SKILL.md"], entries[".claude/skills/demo/SKILL.md"])

    def test_preflight_requires_exact_git_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nested = root / "nested"
            nested.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            with self.assertRaisesRegex(ContractError, "exact Git root"):
                preflight(nested)

    def test_self_check_current_payload(self) -> None:
        result = self_check(ROOT)
        self.assertEqual("valid", result["status"])
        self.assertEqual(SKILL_VERSION, result["version"])

    def test_self_check_rejects_unregistered_payload_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copy_root = Path(temporary) / "payload"
            shutil.copytree(
                ROOT,
                copy_root,
                ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "tests", ".github", "repodocs"),
            )
            self.assertEqual("valid", self_check(copy_root)["status"])
            (copy_root / "templates/unregistered-doc.md").write_text("stray payload file\n", encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "unregistered.*templates/unregistered-doc.md"):
                self_check(copy_root)


class DashboardTests(unittest.TestCase):
    @staticmethod
    def _commit_source(root: Path, text: str) -> str:
        (root / "source.txt").write_text(text, encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "source.txt"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "-c",
                "user.name=Project Context Tests",
                "-c",
                "user.email=tests@example.invalid",
                "commit",
                "-qm",
                text.strip(),
            ],
            check=True,
        )
        return subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_snapshot_reports_current_stale_and_unknown_revision(self) -> None:
        for expected in ("current", "stale", "unknown"):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                subprocess.run(["git", "init", "-q", str(root)], check=True)
                revision = self._commit_source(root, "baseline\n")
                ProjectValidationTests._write_project(
                    root,
                    revision=None if expected == "unknown" else revision,
                    worktree_clean=None if expected == "unknown" else True,
                )
                (root / "CLAUDE.md").write_text(merge_host_text("", "claude"), encoding="utf-8")
                (root / "AGENTS.md").write_text(merge_host_text("", "codex"), encoding="utf-8")
                if expected == "stale":
                    self._commit_source(root, "changed\n")
                self.assertEqual(expected, dashboard_snapshot(root)["project"]["revision_state"])

    def test_snapshot_marks_user_host_edits_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            revision = self._commit_source(root, "baseline\n")
            ProjectValidationTests._write_project(root, revision=revision)
            (root / "CLAUDE.md").write_text(merge_host_text("", "claude"), encoding="utf-8")
            (root / "AGENTS.md").write_text(merge_host_text("", "codex"), encoding="utf-8")
            self.assertEqual("current", dashboard_snapshot(root)["project"]["revision_state"])
            (root / "CLAUDE.md").write_text(
                (root / "CLAUDE.md").read_text(encoding="utf-8") + "\nUser policy changed.\n",
                encoding="utf-8",
            )
            self.assertEqual("stale", dashboard_snapshot(root)["project"]["revision_state"])

    def test_snapshot_preserves_semantic_host_indentation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            claude_path = root / "CLAUDE.md"
            claude_path.write_text(merge_host_text("Rule\n", "claude"), encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "CLAUDE.md"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "-c",
                    "user.name=Project Context Tests",
                    "-c",
                    "user.email=tests@example.invalid",
                    "commit",
                    "-qm",
                    "host baseline",
                ],
                check=True,
            )
            revision = self._commit_source(root, "baseline\n")
            ProjectValidationTests._write_project(root, revision=revision)
            claude_path.write_text(merge_host_text("Rule\n", "claude"), encoding="utf-8")
            (root / "AGENTS.md").write_text(merge_host_text("", "codex"), encoding="utf-8")
            self.assertEqual("current", dashboard_snapshot(root)["project"]["revision_state"])
            claude_path.write_text(merge_host_text("    Rule\n", "claude"), encoding="utf-8")
            self.assertEqual("stale", dashboard_snapshot(root)["project"]["revision_state"])

    def test_snapshot_accepts_canonical_merge_of_tracked_bom_host(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            claude_path = root / "CLAUDE.md"
            claude_path.write_bytes(b"\xef\xbb\xbfRule\n")
            subprocess.run(["git", "-C", str(root), "add", "CLAUDE.md"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "-c",
                    "user.name=Project Context Tests",
                    "-c",
                    "user.email=tests@example.invalid",
                    "commit",
                    "-qm",
                    "BOM host baseline",
                ],
                check=True,
            )
            revision = self._commit_source(root, "baseline\n")
            ProjectValidationTests._write_project(root, revision=revision)
            claude_path.write_bytes(b"\xef\xbb\xbf" + merge_host_text("Rule\n", "claude").encode("utf-8"))
            (root / "AGENTS.md").write_text(merge_host_text("", "codex"), encoding="utf-8")
            self.assertEqual("current", dashboard_snapshot(root)["project"]["revision_state"])

    def test_dashboard_html_escapes_embedded_script_terminator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            revision = self._commit_source(root, "baseline\n")
            manifest = ProjectValidationTests._write_project(root, revision=revision)
            ProjectValidationTests._add_architecture_findings(
                root,
                manifest,
                title='</script><img src=x onerror="alert(1)">',
            )
            finding = finding_document()["findings"][0]
            expected_identity_sha256 = sha256_bytes(
                json.dumps(
                    finding["identity"], ensure_ascii=False, separators=(",", ":"), sort_keys=True
                ).encode("utf-8")
            )
            self.assertEqual(expected_identity_sha256, dashboard_snapshot(root)["findings"][0]["identity_sha256"])
            html = render_dashboard_html(root, nonce="test_nonce").decode("utf-8")
            self.assertNotIn("</script><img", html)
            self.assertIn("\\u003c/script\\u003e", html)

    def test_project_graph_asset_has_safe_accessible_native_svg_contract(self) -> None:
        source = (ROOT / "assets/dashboard.html").read_text(encoding="utf-8")
        required = {
            "native SVG": "document.createElementNS(SVG_NS, tag)",
            "authored counts": "${nodes.length} nodes and ${edges.length} authored directed connections",
            "description": '"aria-describedby": "project-graph-description project-graph-instructions project-graph-receipt"',
            "roving focus": 'tabindex: index === 0 ? "0" : "-1"',
            "keyboard activation": 'event.key === "Enter" || event.key === " "',
            "node identity": "const nodeReference = (node) =>",
            "inspector evidence": "inspector.append(evidence.length ? evidenceList(evidence)",
            "authored reach": "function authoredReach(start, adjacency)",
            "shortest route": "function shortestDirectedPath(start, destination)",
            "directed traversal": "for (const step of forward.get(current) || [])",
            "path control": 'graphButton("Find path"',
            "path helper": "Find path: choose a start node, then a destination.",
            "native tooltips": "button.title = ariaLabel",
            "truncated lane tooltip": "if (lane.group.length > 34) laneLabel.append(svgMake(\"title\", {}, lane.group))",
            "zoom out": 'graphButton("Zoom −", "Zoom out")',
            "zoom in": 'graphButton("Zoom +", "Zoom in")',
            "fit": 'graphButton("Fit", "Fit all project map nodes")',
            "reset": 'graphButton("Reset", "Reset project map view and selection")',
            "pointer pan": 'svg.addEventListener("pointerdown"',
            "keyboard pan": "if (event.target !== svg) return;",
            "list fallback": 'const fallback = make("details", "graph-list-fallback")',
            "list fallback label": 'make("summary", "", "Accessible list view")',
            "focused context explorer": 'renderContextExplorer(byId("context-map-panel"), snapshot.context_map',
            "cycle guard": "const visited = new Set([start]);",
            "self loop and parallel edge route": "const geometry = edgeGeometry(edge);",
            "unique authored edge channel": "edge.channelIndex * EDGE_CHANNEL_GAP",
            "node-safe edge corridor": "routeEndX: x + NODE_WIDTH + corridorWidth - 4",
            "missing group": 'asText(node.group, "Ungrouped")',
            "parallel edge order": "compareText(left.edge.label, right.edge.label) || left.edge.index - right.edge.index",
        }
        for feature, token in required.items():
            with self.subTest(feature=feature):
                self.assertIn(token, source)
        for sink in (
            r"\.(?:inner|outer)HTML\b",
            r"\binsertAdjacentHTML\b",
            r"\b(?:eval|fetch)\s*\(",
            r"\bnew\s+Function\b",
            r"\b(?:XMLHttpRequest|WebSocket|EventSource|WebTransport)\b",
            r"\b(?:navigator\.sendBeacon|window\.open|document\.write|location\.(?:assign|replace))\s*\(",
            r"<(?:script|link)\b[^>]*(?:src|href)\s*=\s*[\"'](?!data:)",
            r"\bforeignObject\b",
        ):
            with self.subTest(forbidden_sink=sink):
                self.assertNotRegex(source, sink)
        self.assertNotIn("Trace route", source)

    def test_dashboard_workspace_navigation_and_typed_attention_drilldown_contract(self) -> None:
        source = (ROOT / "assets/dashboard.html").read_text(encoding="utf-8")
        for token in (
            'aria-label="Project Context workspaces"',
            '<a data-mode="monitor" href="#attention">Monitor</a>',
            '<a data-mode="remediate" href="#findings">Remediate',
            '<a data-mode="explore" href="#project-map">Explore</a>',
            '<a data-mode="govern" href="#decisions">Govern</a>',
            'title="Re-read validated artifacts; does not run an audit"',
            'title="Show authored project topology"',
            'title="Browse validated context relationships"',
            'title="Clear all finding filters"',
            'repositoryName.title = repositoryName.textContent',
            'repositoryRevision.title = asText(project.revision, repositoryRevision.textContent)',
            'contextAction.title = "Open this finding\'s evidence artifact in Context Explorer"',
            '<li><a href="#project-map">Project topology</a></li>',
            '<li><a href="#context-map">Context documents</a></li>',
            'id="attention-inspector" aria-labelledby="attention-inspector-title"',
            'systemAttention.push({kind: "integrity"',
            '({kind: "finding", finding})',
            'const button = make("button", "attention-item")',
            'button.setAttribute("aria-controls", "attention-inspector")',
            'button.setAttribute("aria-pressed", "false")',
            'window.matchMedia("(max-width: 1120px)").matches',
            'inspector.scrollIntoView({block: "start"})',
            'if (item.kind !== "finding")',
            'openFinding.addEventListener("click", () => openFullFinding(finding))',
            'item.details.open = true',
            'navigateWorkspace("#findings", () => {',
            'item.details.querySelector("summary").focus()',
            'navigateWorkspace("#context-map", () => {',
            'activateMapTab(route.mapTab)',
            'document.querySelector(".skip-link").addEventListener("click", (event) => {',
            'form.setAttribute("action", `${window.location.pathname}${window.location.hash || "#attention"}`)',
            'window.addEventListener("hashchange", update)',
            'const views = new Map([',
            '["#attention", {view: "attention", section: "overview"}]',
            '["#project-map", {view: "map", section: "map", mapTab: "project"}]',
            '["#context-map", {view: "map", section: "map", mapTab: "context"}]',
            'byId(id).hidden = id !== route.section',
            'byId("main").dataset.view = route.view',
            'link.setAttribute("aria-current", "location")',
            'link.setAttribute("aria-current", "page")',
            'const selected = buttons.find((entry) => asText(entry.node.id) === selectedNodeId)',
            'selectNode(asText(visible[0].node.id), false)',
            'emptyState("No matching context artifact"',
            'button.title = nodePath(node)',
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)
        self.assertNotIn('row.addEventListener("click"', source)

    def test_focused_context_groups_pairs_but_preserves_raw_wikilink_occurrences(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            revision = self._commit_source(root, "baseline\n")
            manifest = ProjectValidationTests._write_project(root, revision=revision)
            documents = {
                "PROJECT_CONTEXT.md": (
                    '# Context\n\n<a id="self"></a>\n'
                    '[[architecture]] [[architecture#target]] [[architecture#target|again]] [[context#self]]\n'
                ),
                "repodocs/architecture.md": (
                    '# Architecture\n\n<a id="target"></a>\n[[context]] [[context#self]]\n'
                ),
            }
            for relative, text in documents.items():
                (root / relative).write_text(text, encoding="utf-8")
                next(item for item in manifest["artifacts"] if item["path"] == relative)["sha256"] = sha256_text(text)
            (root / "repodocs/project-context.manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )

            snapshot = dashboard_snapshot(root)
            edges = snapshot["context_map"]["edges"]
            self.assertEqual(6, snapshot["integrity"]["wikilinks"])
            self.assertEqual(6, len(edges))
            self.assertEqual(
                Counter({("context", "architecture"): 3, ("architecture", "context"): 2, ("context", "context"): 1}),
                Counter((edge["from"], edge["to"]) for edge in edges),
            )
            self.assertEqual(
                Counter({"links to": 1, "links to #target": 2}),
                Counter(edge["label"] for edge in edges if edge["from"] == "context" and edge["to"] == "architecture"),
            )

        source = (ROOT / "assets/dashboard.html").read_text(encoding="utf-8")
        index_source = source.split("function contextRelationshipIndex", 1)[1].split(
            "const contextRelationships", 1
        )[0]
        explorer_source = source.split("function renderContextExplorer", 1)[1].split(
            "function renderProjectGraph", 1
        )[0]
        for token in (
            "const key = JSON.stringify([from, to])",
            "pair.occurrences += 1",
            "pair.labels.add",
            "pair.evidence.push",
            "if (outgoing.has(pair.from))",
            "if (incoming.has(pair.to))",
            "repeated: Math.max(0, edges.length - pairs.size)",
        ):
            with self.subTest(token=token):
                self.assertIn(token, index_source)
        for token in (
            'return "Finding records"',
            'return "Audit records"',
            'return "Governance"',
            'return "Canonical context"',
            "${graph.edges.length} validated raw wikilink occurrences remain available as evidence",
            "${pair.occurrences} validated occurrence",
            '[...pair.labels].sort().join(", ")',
            "const directPairs = [...new Set([...outgoing, ...incoming])]",
        ):
            with self.subTest(token=token):
                self.assertIn(token, explorer_source)
        self.assertNotIn("Manifest-owned validated artifact.", explorer_source)
        self.assertNotIn('renderMap(byId("context-map-panel")', source)

    def test_findings_ai_prompt_has_safe_state_aware_lifecycle_contract(self) -> None:
        source = (ROOT / "assets/dashboard.html").read_text(encoding="utf-8")
        prompt_source = source.split("function remediationPrompt", 1)[1].split("function findingSearchText", 1)[0]
        for token in (
            '<th class="ai-prompt-cell" scope="col">AI Prompt</th>',
            'kind: "recheck"',
            'kind: "fix"',
            'kind: "resolved"',
            'kind: "refuted"',
            ".finding-details > summary",
            "STOP: this snapshot is stale or its freshness is unknown",
            "Keep this historical id refuted forever",
            "a comparable re-audit may return this resolved id to persisting",
            'prompt_contract: "project-context-remediation-v1"',
            "identity_sha256: asText(finding.identity_sha256)",
            "Recompute identity_sha256 from the canonical record's complete identity object",
            "evidence_locations: promptLocations(finding.evidence)",
            "validate each completed auditor candidate against its own saved --previous file",
            "previous findings scope, candidate findings scope, and candidate inventory scope",
            "validate-findings --input <candidate-findings.json> --previous <saved-previous-findings.json>",
            "validate-inventory --input <candidate-inventory.json> --previous <saved-previous-inventory.json>",
            "Every completed auditor needs a findings document with the new run_id",
            "A fresh read-only agent must perform blind verification",
            "findings, trace ledger, decision rationale",
            "write the manifest last",
            "navigator.clipboard.writeText(prompt)",
            "field.select()",
            "search: findingSearchText(finding)",
            "cell.colSpan = 8",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)
        for forbidden in (
            "title: asText(finding.title)",
            "assertion: asText(identity.assertion)",
            "symbol: asText(identity.symbol)",
            "detail: asText",
            "note: asText(verification.note)",
            "navigator.clipboard.read",
            "document.execCommand",
            "button.disabled = true",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, prompt_source)
        self.assertNotIn(".finding-details summary", source)

    def test_findings_master_prompt_binds_selected_active_snapshot_safely(self) -> None:
        source = (ROOT / "assets/dashboard.html").read_text(encoding="utf-8")
        master_source = source.split("function masterFindingBinding", 1)[1].split("function findingSearchText", 1)[0]
        for token in (
            'id="select-visible-findings"',
            'id="copy-master-prompt"',
            'id="master-prompt-preview"',
            'class="selection-control"',
            ".selection-control { display: inline-grid; width: 44px; height: 44px",
            "const selectedFindingIds = new Set()",
            "item.checkbox && !item.row.hidden",
            "selectVisible.indeterminate",
            'contextState !== "valid"',
            'preview.addEventListener("toggle"',
            "cell.colSpan = 8",
            'prompt_contract: "project-context-master-remediation-v1"',
            "expected_active_count: activeFindings.length",
            "expected_active_findings: activeFindings.map",
            "selected_findings: selected.map",
            "identity_sha256: asText(finding.identity_sha256)",
            "const MASTER_PROMPT_LIMITS = Object.freeze",
            "new TextEncoder().encode(prompt).byteLength",
            "Do not queue or dispatch work, create worktrees, edit code",
            "the complete sorted active tuple set",
            "never silently add or drop findings",
            "Build an in-memory queue for the selected active IDs only",
            "Before editing any selected original high/critical claim",
            "the coordinator builds a conflict graph and assigns exclusive ownership",
            "Workers never edit repodocs, inventory, project map, or manifest",
            "Run `./tests/run --fast` only when it exists",
            "otherwise record coverage-incomplete",
            "before auditor dispatch",
            "--allow-provisional",
            "including newly discovered ones",
            "repodocs/project-map.json uses the new run_id",
            "preserve map nodes/edges unless evidence-backed topology changed",
            "Withhold findings, this bulk payload, trace ledger",
            "It may not execute project code, hooks, package managers, builds, tests, linters, plugins, generators, repository-configured tools, or use the network",
            "Before any write, validate final findings",
            "write repodocs/project-context.manifest.json last",
            "Do not commit, push, open a PR, or deploy",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)
        for forbidden in (
            "title: asText(finding.title)",
            "assertion: asText(identity.assertion)",
            "symbol: asText(identity.symbol)",
            "detail: asText",
            "note: asText(verification.note)",
            "expected_active_count: 59",
            "navigator.clipboard.read",
            "document.execCommand",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, master_source)
        self.assertLess(
            master_source.index("activeFindings.length > MASTER_PROMPT_LIMITS.findings"),
            master_source.index("let locationCount = 0"),
        )
        self.assertIn("if (locationCount > MASTER_PROMPT_LIMITS.locations)", master_source)
        command_order = [
            "validate-findings --input <candidate-findings.json>",
            "validate-project-map --input <candidate-project-map.json>",
            "validate-inventory --input <post-blind-candidate-inventory.json>",
            "validate-manifest --input <candidate-manifest.json>",
            "validate-project --repo <exact-root>",
        ]
        positions = [master_source.index(command) for command in command_order]
        self.assertEqual(positions, sorted(positions))

    def test_project_graph_snapshot_preserves_adversarial_topology_safely(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            revision = self._commit_source(root, "baseline\n")
            manifest = ProjectValidationTests._write_project(root, revision=revision)
            hostile = '</script><img src=x onerror="alert(1)">'
            evidence = [{"path": "src/module", "detail": "Authored edge."}]
            value = project_map()
            value["nodes"] = [
                {"id": "alpha", "label": hostile, "kind": "component", "status": "current", "evidence": evidence},
                {"id": "beta", "label": hostile, "kind": "component", "status": "current", "evidence": evidence},
                {
                    "id": "gamma",
                    "label": "Cycle",
                    "group": "Runtime",
                    "kind": "runtime",
                    "status": "current",
                    "evidence": evidence,
                },
            ]
            value["edges"] = [
                {"from": "alpha", "to": "alpha", "label": "self", "evidence": evidence},
                {"from": "alpha", "to": "beta", "label": "calls", "evidence": evidence},
                {"from": "alpha", "to": "beta", "label": "reads", "evidence": evidence},
                {"from": "beta", "to": "alpha", "label": "calls", "evidence": evidence},
                {"from": "beta", "to": "gamma", "label": "cycles", "evidence": evidence},
                {"from": "gamma", "to": "alpha", "label": "cycles", "evidence": evidence},
            ]
            validate_project_map(value)
            raw = json.dumps(value, sort_keys=True).encode()
            (root / "repodocs/project-map.json").write_bytes(raw)
            next(item for item in manifest["artifacts"] if item["id"] == "project_map")["sha256"] = sha256_bytes(raw)
            (root / "repodocs/project-context.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            self.assertEqual({"nodes": value["nodes"], "edges": value["edges"]}, dashboard_snapshot(root)["project_map"])
            html = render_dashboard_html(root, nonce="test_nonce").decode("utf-8")
            self.assertNotIn(hostile, html)
            self.assertIn("\\u003c/script\\u003e", html)

    def test_markdown_sections_mark_truncated_bodies_explicitly(self) -> None:
        long_entry = "## ADR-100: Long\n" + "\n".join(f"- body line {index}" for index in range(10)) + "\n"
        sections = _markdown_sections(long_entry, "ADR")
        self.assertEqual(9, len(sections[0]["lines"]))
        self.assertTrue(sections[0]["lines"][-1].startswith("…"))
        self.assertIn("truncated", sections[0]["lines"][-1])
        exact_entry = "## ADR-101: Exact\n" + "\n".join(f"- body line {index}" for index in range(8)) + "\n"
        sections = _markdown_sections(exact_entry, "ADR")
        self.assertEqual(8, len(sections[0]["lines"]))
        self.assertFalse(any("truncated" in line for line in sections[0]["lines"]))

    def test_markdown_sections_ignore_anchors_in_canonical_multi_entry_documents(self) -> None:
        # The canonical template puts the NEXT entry's <a id> anchor above its heading;
        # an 8-line entry followed by an anchor must not read as truncated, and the
        # anchor must never surface as body text of the previous entry.
        canonical = (
            '<a id="ADR-100"></a>\n## ADR-100: First\n'
            + "\n".join(f"- bullet {index}" for index in range(8))
            + '\n\n<a id="ADR-101"></a>\n## ADR-101: Second\n- short body\n'
        )
        sections = _markdown_sections(canonical, "ADR")
        self.assertEqual(["ADR-100", "ADR-101"], [section["id"] for section in sections])
        self.assertEqual(8, len(sections[0]["lines"]))
        self.assertFalse(any("truncated" in line for line in sections[0]["lines"]))
        self.assertNotIn("<a id=", sections[0]["summary"])
        self.assertEqual(["- short body"], sections[1]["lines"])

    def test_instruction_view_resolves_artifacts_and_redacts_config_previews(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "CLAUDE.md").write_text(
                "See repodocs/architecture.md and repodocs/architecture.md#shape.\n"
                "Data: repodocs/project-map.json and repodocs/project-map.json#frag.\n"
                "Gone: repodocs/missing.md\n",
                encoding="utf-8",
            )
            (root / ".claude").mkdir()
            (root / ".claude/settings.local.json").write_text(
                '{"apiKey": "sk-SECRETVALUE", "docs": "repodocs/architecture.md"}\n', encoding="utf-8"
            )
            entries = [{"path": "CLAUDE.md"}, {"path": ".claude/settings.local.json"}]
            markdown = {"repodocs/architecture.md": 'Intro\n<a id="shape"></a>\n'}
            artifact_paths = {"repodocs/architecture.md", "repodocs/project-map.json"}
            view = _instruction_view(root, entries, markdown, artifact_paths)
            statuses = {link["raw"]: link["status"] for link in view[0]["links"]}
            self.assertEqual(
                {
                    "repodocs/architecture.md": "resolves",
                    "repodocs/architecture.md#shape": "resolves",
                    "repodocs/project-map.json": "resolves",
                    "repodocs/project-map.json#frag": "dangling-anchor",
                    "repodocs/missing.md": "dangling-file",
                },
                statuses,
            )
            self.assertIn("repodocs/architecture.md", view[0]["preview"])
            self.assertNotIn("preview_redacted", view[0])
            # Host configuration values (credentials) never reach the snapshot.
            self.assertEqual("", view[1]["preview"])
            self.assertTrue(view[1]["preview_redacted"])
            self.assertNotIn("sk-SECRETVALUE", json.dumps(view))
            self.assertEqual(
                {"repodocs/architecture.md": "resolves"},
                {link["raw"]: link["status"] for link in view[1]["links"]},
            )
            unverified = _instruction_view(root, entries, None)
            self.assertTrue(all(link["status"] == "unverified" for item in unverified for link in item["links"]))

    def test_snapshot_redacts_config_previews_and_resolves_ownership_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            revision = self._commit_source(root, "baseline\n")
            ProjectValidationTests._write_project(root, revision=revision)
            (root / "CLAUDE.md").write_text(
                merge_host_text(
                    "Map: repodocs/project-map.json - owned by repodocs/project-context.manifest.json\n", "claude"
                ),
                encoding="utf-8",
            )
            (root / "AGENTS.md").write_text(merge_host_text("", "codex"), encoding="utf-8")
            (root / ".claude").mkdir()
            (root / ".claude/settings.json").write_text('{"token": "sk-EXTREMELY-SECRET"}\n', encoding="utf-8")
            snapshot = dashboard_snapshot(root)
            self.assertEqual("valid", snapshot["context"]["state"])
            entries = {entry["path"]: entry for entry in snapshot["agent_instructions"]}
            self.assertTrue(entries[".claude/settings.json"]["preview_redacted"])
            self.assertNotIn("sk-EXTREMELY-SECRET", json.dumps(snapshot))
            statuses = {link["raw"]: link["status"] for link in entries["CLAUDE.md"]["links"]}
            self.assertEqual("resolves", statuses["repodocs/project-map.json"])
            self.assertEqual("resolves", statuses["repodocs/project-context.manifest.json"])
            self.assertNotIn("sk-EXTREMELY-SECRET", render_dashboard_html(root, nonce="test_nonce").decode("utf-8"))

    def test_http_boundary_serves_only_the_token_route(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            revision = self._commit_source(root, "baseline\n")
            ProjectValidationTests._write_project(root, revision=revision)
            (root / "CLAUDE.md").write_text(merge_host_text("", "claude"), encoding="utf-8")
            (root / "AGENTS.md").write_text(merge_host_text("", "codex"), encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _dashboard_handler_class(root, "/token123/"))
            server.daemon_threads = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                def request(method: str, target: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=30)
                    try:
                        connection.request(method, target, headers=headers or {})
                        response = connection.getresponse()
                        return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
                    finally:
                        connection.close()

                status, headers, body = request("GET", "/token123/")
                self.assertEqual(200, status)
                self.assertIn("default-src 'none'", headers["content-security-policy"])
                self.assertEqual("nosniff", headers["x-content-type-options"])
                self.assertEqual("no-store", headers["cache-control"])
                self.assertIn(b"Project Context", body)
                for target in ("/", "/token123", "/token123/?probe=1", "/other/", "/token123/../"):
                    with self.subTest(target=target):
                        self.assertEqual(404, request("GET", target)[0])
                status, headers, _ = request("POST", "/token123/")
                self.assertEqual(405, status)
                self.assertEqual("GET, HEAD", headers["allow"])
                self.assertEqual(400, request("GET", "/token123/", {"Host": "evil.example"})[0])
                # HEAD over a raw socket: http.client discards HEAD bodies client-side,
                # so only the wire proves the server sent headers and nothing else.
                with socket.create_connection(("127.0.0.1", server.server_port), timeout=30) as raw:
                    raw.sendall(
                        f"HEAD /token123/ HTTP/1.1\r\nHost: 127.0.0.1:{server.server_port}\r\n"
                        "Connection: close\r\n\r\n".encode()
                    )
                    wire = b""
                    while chunk := raw.recv(65536):
                        wire += chunk
                head, separator, rest = wire.partition(b"\r\n\r\n")
                self.assertEqual(b"200", head.split(b"\r\n")[0].split(b" ")[1])
                self.assertEqual(b"\r\n\r\n", separator)
                self.assertEqual(b"", rest)
                declared = re.search(rb"content-length: (\d+)", head, re.IGNORECASE)
                self.assertIsNotNone(declared)
                assert declared is not None
                self.assertGreater(int(declared.group(1)), 1000)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=10)


class CliTests(unittest.TestCase):
    """The CLI contract: exit codes 0/4/70, JSON errors on stderr, exact stdout bytes."""

    @staticmethod
    def _run(*argv: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run([sys.executable, str(SCRIPT), *argv], capture_output=True, timeout=60)

    def test_self_check_exits_zero(self) -> None:
        result = self._run("self-check", "--skill-root", str(ROOT))
        self.assertEqual(0, result.returncode)
        self.assertEqual("valid", json.loads(result.stdout)["status"])

    def test_merge_host_refuses_missing_or_empty_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            empty = Path(temporary) / "CLAUDE.md"
            empty.write_bytes(b"")
            for extra in (["--input", str(empty)], []):
                result = self._run("merge-host", "--host", "claude", *extra)
                self.assertEqual(4, result.returncode)
                self.assertIn("allow-create", json.loads(result.stderr)["error"])

    def test_merge_host_allow_create_writes_exact_block(self) -> None:
        result = self._run("merge-host", "--host", "claude", "--allow-create")
        self.assertEqual(0, result.returncode)
        self.assertEqual(merge_host_text("", "claude").encode("utf-8"), result.stdout)

    def test_merge_host_preserves_bom_and_user_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "CLAUDE.md"
            source.write_bytes(b"\xef\xbb\xbf# Mine\n")
            result = self._run("merge-host", "--host", "claude", "--input", str(source))
            self.assertEqual(0, result.returncode)
            self.assertTrue(result.stdout.startswith(b"\xef\xbb\xbf"))
            self.assertIn(b"# Mine", result.stdout)

    def test_merge_host_out_of_order_markers_fail_cleanly(self) -> None:
        begin, end = HOST_MARKERS["claude"]
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "CLAUDE.md"
            source.write_text(f"{end}\nuser text\n{begin}\n", encoding="utf-8")
            result = self._run("merge-host", "--host", "claude", "--input", str(source))
            self.assertEqual(4, result.returncode)
            self.assertIn("out of order", json.loads(result.stderr)["error"])

    def test_validate_project_map_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "project-map.json"
            source.write_text(json.dumps(project_map()), encoding="utf-8")
            result = self._run("validate-project-map", "--input", str(source))
            self.assertEqual(0, result.returncode)
            self.assertEqual(
                {"status": "valid", "nodes": 1, "edges": 0},
                json.loads(result.stdout),
            )

    @unittest.skipIf(os.name == "nt", "install.sh requires a POSIX shell")
    def test_installer_reports_missing_release_tag(self) -> None:
        # The v0.5.1 headline fix: under pipefail an empty tag pipeline used to kill the
        # script before its own error message; the guard must now actually be reached.
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            (source / "README.md").write_text("a repository with no release tags\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(source), "add", "README.md"], check=True)
            subprocess.run(
                ["git", "-C", str(source), "-c", "user.name=Project Context Tests",
                 "-c", "user.email=tests@example.invalid", "commit", "-qm", "init"],
                check=True,
            )
            result = subprocess.run(
                ["bash", str(ROOT / "install.sh")],
                capture_output=True,
                timeout=120,
                env={
                    **os.environ,
                    "PROJECT_CONTEXT_REPO": str(source),
                    "PROJECT_CONTEXT_HOME": str(Path(temporary) / "home"),
                    "PROJECT_CONTEXT_VERSION": "",
                },
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("could not resolve a release tag", result.stderr.decode())

    def test_install_ps1_mirrors_posix_installer_guarantees(self) -> None:
        # install.ps1 has no automated execution anywhere (Windows CI is deliberately out of
        # the default pipeline), so its mirror-critical invariants are pinned textually here.
        posix = (ROOT / "install.sh").read_text(encoding="utf-8")
        windows = (ROOT / "install.ps1").read_text(encoding="utf-8")
        # Both installers report the two tag-resolution failures distinctly.
        for message in ("could not reach", "could not resolve a release tag"):
            self.assertIn(message, posix)
            self.assertIn(message, windows)
        # Both extend PATH with the per-user bin directory BEFORE the first jCodeMunch
        # lookup, and honour PROJECT_CONTEXT_HOME rather than hardcoding $HOME there.
        self.assertLess(posix.index('export PATH="$HOME_DIR/.local/bin'), posix.index("command -v jcodemunch-mcp"))
        self.assertLess(windows.index('Join-Path $HomeDir ".local\\bin"'), windows.index("Get-Command jcodemunch-mcp"))
        self.assertNotIn("$HOME\\.local\\bin", windows)

    @unittest.skipIf(
        os.name == "nt",
        "install.sh test is POSIX-only; install.ps1 is never executed by CI (Windows is deliberately "
        "out of the default pipeline) - its invariants are pinned by the mirror test and RELEASING.md's "
        "manual Windows check",
    )
    def test_installer_installs_updates_and_archives_legacy(self) -> None:
        tags = subprocess.run(["git", "-C", str(ROOT), "tag", "-l", "v*"], capture_output=True, text=True)
        if not tags.stdout.strip():
            self.skipTest("no release tags in this clone (shallow CI checkout); the installer CI job covers it")
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            legacy = home / ".claude/skills/project-context"
            (legacy / "auditors").mkdir(parents=True)
            (legacy / "SKILL.md").write_text("legacy full clone\n", encoding="utf-8")
            fake_bin = home / "fakebin"
            fake_bin.mkdir()
            fake_tool = fake_bin / "jcodemunch-mcp"
            fake_tool.write_text(
                '#!/bin/sh\necho "$@" >> "$(dirname "$0")/calls.log"\n'
                '[ "$1" = "--version" ] && echo "jcodemunch-mcp 9.9.9"\nexit 0\n',
                encoding="utf-8",
            )
            fake_tool.chmod(0o755)
            environment = {
                **os.environ,
                "PROJECT_CONTEXT_HOME": str(home),
                "PROJECT_CONTEXT_REPO": str(ROOT),
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
            }
            for _ in range(2):  # second run exercises the update path
                result = subprocess.run(
                    ["bash", str(ROOT / "install.sh")], capture_output=True, env=environment, timeout=300
                )
                self.assertEqual(0, result.returncode, result.stderr.decode())
            payload = home / ".agents/skills/project-context"
            self.assertTrue((payload / "VERSION").is_file())
            adapter = (home / ".claude/skills/project-context/SKILL.md").read_text(encoding="utf-8")
            self.assertIn("project-context", adapter)
            backups = list((home / ".skill-backups").iterdir())
            self.assertEqual(1, len(backups))
            self.assertTrue((backups[0] / "auditors").is_dir())
            calls = (fake_bin / "calls.log").read_text(encoding="utf-8")
            self.assertIn("--version", calls)
            self.assertIn("init --client auto --yes", calls)

    def test_previous_sha256_guards_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            previous = Path(temporary) / "previous.json"
            previous.write_text(json.dumps(finding_document()), encoding="utf-8")
            current = Path(temporary) / "current.json"
            document = finding_document()
            document["findings"][0]["status"] = "persisting"
            current.write_text(json.dumps(document), encoding="utf-8")
            good = sha256_bytes(previous.read_bytes())
            result = self._run("validate-findings", "--input", str(current), "--previous", str(previous), "--previous-sha256", good)
            self.assertEqual(0, result.returncode, result.stderr.decode())
            bad = "sha256:" + "0" * 64
            result = self._run("validate-findings", "--input", str(current), "--previous", str(previous), "--previous-sha256", bad)
            self.assertEqual(4, result.returncode)
            self.assertIn("unknown provenance", json.loads(result.stderr)["error"])

    def test_missing_repo_is_user_correctable(self) -> None:
        result = self._run("preflight", "--repo", "/nonexistent/project-context-missing")
        self.assertEqual(4, result.returncode)
        self.assertIn("does not exist", json.loads(result.stderr)["error"])


if __name__ == "__main__":
    unittest.main()
