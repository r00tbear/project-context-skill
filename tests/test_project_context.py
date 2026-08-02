import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts.project_context import (
    HOST_MARKERS,
    ContractError,
    extract_host_block,
    merge_host_text,
    preflight,
    safe_path,
    self_check,
    sha256_bytes,
    sha256_text,
    strict_json_loads,
    validate_config,
    validate_findings,
    validate_inventory,
    validate_manifest,
    validate_project,
    validate_relative_path,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/project_context.py"
SKILL_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


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


def finding_document(severity: str = "medium") -> dict[str, Any]:
    verification = "confirmed" if severity in {"high", "critical"} else "not-required"
    return {
        "schema_version": 1,
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


def inventory() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "runs": [
            {
                "id": "run-20260802-1",
                "scanned_at": "2026-08-02T12:00:00+00:00",
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
            }
        ],
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
    def _write_project(root: Path) -> dict[str, Any]:
        (root / "repodocs/audit").mkdir(parents=True)
        config_value = config()
        config_raw = (json.dumps(config_value, indent=2, sort_keys=True) + "\n").encode()
        (root / "repodocs/project-context.config.json").write_bytes(config_raw)

        inventory_value = inventory()
        inventory_value["runs"][0]["outcome"] = "coverage-incomplete"
        inventory_value["runs"][0]["coverage"]["completed"] = []
        inventory_raw = (json.dumps(inventory_value, indent=2, sort_keys=True) + "\n").encode()
        (root / "repodocs/audit/inventory.json").write_bytes(inventory_raw)

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
            with self.assertRaisesRegex(ContractError, "installed skill"):
                validate_project(root)
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
            ProjectValidationTests._write_project(root)
            (root / "CLAUDE.md").write_text(merge_host_text("", "claude"), encoding="utf-8")
            (root / "AGENTS.md").write_text(merge_host_text("", "codex"), encoding="utf-8")
            self.assertEqual("greenfield", preflight(root)["source_state"])
            (root / "repodocs/architecture.md").write_text("drifted\n", encoding="utf-8")
            self.assertEqual("codebase", preflight(root)["source_state"])

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

    def test_preflight_rejects_symlink_under_repodocs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "repodocs").mkdir()
            outside = root.parent / f"{root.name}-target"
            outside.write_text("", encoding="utf-8")
            try:
                (root / "repodocs/linked.md").symlink_to(outside)
                with self.assertRaisesRegex(ContractError, "symlinks are not allowed"):
                    preflight(root)
            finally:
                outside.unlink()

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

    def test_missing_repo_is_user_correctable(self) -> None:
        result = self._run("preflight", "--repo", "/nonexistent/project-context-missing")
        self.assertEqual(4, result.returncode)
        self.assertIn("does not exist", json.loads(result.stderr)["error"])


if __name__ == "__main__":
    unittest.main()
