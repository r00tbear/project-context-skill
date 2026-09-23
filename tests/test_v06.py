import copy
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.project_context import (
    ContractError,
    _markdown_sections,
    archive_legacy,
    dashboard_snapshot,
    drift,
    extract_host_block,
    merge_host_text,
    preview_context,
    sha256_bytes,
    sha256_text,
    task_brief,
    validate_inventory,
    validate_manifest,
    validate_project,
    validate_remediation,
)

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text().strip()
SCOPE = {"included": ["."], "excluded": ["repodocs"], "unscanned": []}
AUDITORS = ["stack", "architecture", "bloat", "security", "testing"]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def fixture(root, *, greenfield=False, context=False):
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    source = root / "src/main.py"
    if not greenfield:
        source.parent.mkdir(exist_ok=True)
        source.write_text("value = 1\n")
    run_id = "audit-20260922T120000Z-one"
    scope = copy.deepcopy(SCOPE)
    if greenfield:
        scope = {"included": ["."], "excluded": ["repodocs"], "unscanned": []}
    config = {
        "schema_version": 1,
        "user_level": "specialist",
        "language": "en",
        "document_layout": "full",
        "domains": {"ui": "disabled", "data": "disabled"},
        "hosts": {"claude": True, "codex": True},
        "audit": {"exclude": []},
    }
    config_path = root / "repodocs/project-context.config.json"
    write_json(config_path, config)
    artifacts = {}

    def own(artifact_id, path, content=None, sections=None):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if content is not None:
            target.write_text(content)
        item = {
            "id": artifact_id,
            "path": path,
            "kind": "owned_file",
            "sha256": sha256_text(target.read_text()),
        }
        if sections is not None:
            item["sections"] = sections
        artifacts[artifact_id] = item

    findings = {}
    results = {}
    for auditor in ["greenfield"] if greenfield else AUDITORS:
        rows = []
        if auditor == "architecture":
            rows.append(
                {
                    "id": "architecture-001",
                    "kind": "boundary",
                    "title": "Module mixes responsibilities",
                    "severity": "high",
                    "confidence": "high",
                    "identity": {
                        "path": "src/main.py",
                        "assertion": "mixed responsibilities",
                    },
                    "status": "new",
                    "evidence": [
                        {"path": "src/main.py", "line": 1, "detail": "Shared module."}
                    ],
                    "verification": {
                        "status": "confirmed",
                        "resulting_severity": "high",
                        "counterevidence": [],
                        "note": "Confirmed.",
                    },
                    "remediation": {
                        "observable_effect": "Responsibilities are mixed.",
                        "change_boundary": "src/main.py",
                        "done_when": "Separate responsibilities with a focused regression.",
                        "uncertainties": ["Unknown consumers."],
                    },
                }
            )
        document = {
            "schema_version": 3,
            "run_id": run_id,
            "auditor": auditor,
            "scanned_at": "2026-09-22T12:00:00Z",
            "scope": copy.deepcopy(scope),
            "findings": rows,
        }
        path = f"repodocs/audit/findings/{auditor}.json"
        write_json(root / path, document)
        own(f"finding_{auditor}", path)
        findings[auditor] = document
        paths = [] if greenfield else ["src/main.py"]
        results[auditor] = {
            "origin": "current",
            "source_run_id": run_id,
            "scanned_at": document["scanned_at"],
            "sha256": artifacts[f"finding_{auditor}"]["sha256"],
            "scope": copy.deepcopy(scope),
            "covered_paths": paths,
            "sources": [
                {"path": path, "sha256": sha256_bytes((root / path).read_bytes())}
                for path in paths
            ],
        }
    run = {
        "id": run_id,
        "scanned_at": "2026-09-22T12:00:00Z",
        "revision": None,
        "worktree_clean": False,
        "source_state": "greenfield" if greenfield else "codebase",
        "outcome": "complete",
        "domains": {"ui": "absent", "data": "absent"},
        "coverage": {
            "required": list(results),
            "completed": list(results),
            "skipped": {},
            "failed": [],
        },
        "scope": copy.deepcopy(scope),
        "tools": {"jcodemunch": "used"},
        "verification": {"blind": "passed" if context else "not-run", "issues": 0},
        "results": results,
    }
    inventory = {"schema_version": 3, "runs": [run]}
    report_path = f"repodocs/audit/reports/{run_id}.md"
    own(
        "audit_report",
        report_path,
        "# Audit\n\n## Requirements\n\n- Confirmed requirement.\n\n## Open questions\n\n- None.\n",
    )
    if context:
        section = {
            "id": "overview",
            "source_run_id": run_id,
            "covered_paths": [] if greenfield else ["src/main.py"],
            "coverage_sha256": sha256_bytes(
                json.dumps(
                    [] if greenfield else ["src/main.py"], separators=(",", ":")
                ).encode()
            ),
            "sources": []
            if greenfield
            else [{"path": "src/main.py", "sha256": sha256_bytes(source.read_bytes())}],
        }
        for artifact_id, path in [
            ("context", "PROJECT_CONTEXT.md"),
            ("decisions", "repodocs/decisions.md"),
            ("legacy_warning", "repodocs/LegacyWarning.md"),
            ("migration_backlog", "repodocs/migration-backlog.md"),
            ("drift_report", "repodocs/audit/drift-report.md"),
            ("architecture", "repodocs/architecture.md"),
            ("techstack", "repodocs/techstack.md"),
            ("security", "repodocs/security.md"),
            ("testing", "repodocs/testing.md"),
            ("edge_cases", "repodocs/edge-cases.md"),
        ]:
            content = "# Guidance\n"
            if artifact_id == "decisions":
                content = '# Decisions\n\n<a id="ADR-001"></a>\n## ADR-001: Keep current shape\n- Status: accepted\n- Sources: architecture-001\n'
            own(artifact_id, path, content, [copy.deepcopy(section)])
        path = "repodocs/project-map.json"
        write_json(
            root / path,
            {"schema_version": 1, "run_id": run_id, "nodes": [], "edges": []},
        )
        own("project_map", path)
        for host, path in [("claude", "CLAUDE.md"), ("codex", "AGENTS.md")]:
            text = merge_host_text("# Existing user note\n", host)
            (root / path).write_text(text)
            artifacts[f"{host}_host"] = {
                "id": f"{host}_host",
                "path": path,
                "kind": "managed_block",
                "sha256": sha256_text(extract_host_block(text, host)),
            }
    write_json(root / "repodocs/audit/inventory.json", inventory)
    own("audit_inventory", "repodocs/audit/inventory.json")
    manifest = {
        "schema_version": 2,
        "skill_version": VERSION,
        "profile": "context" if context else "audit",
        "context_run_id": run_id if context else None,
        "config_sha256": sha256_text(config_path.read_text()),
        "reserved_ids": [],
        "domains": ["architecture", "stack", "security", "testing"] if context else [],
        "hosts": ["claude", "codex"] if context else [],
        "artifacts": list(artifacts.values()),
    }
    write_json(root / "repodocs/project-context.manifest.json", manifest)
    return inventory, manifest


class V06Tests(unittest.TestCase):
    def test_audit_to_context_requires_explicit_regeneration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, audit_manifest = fixture(root)
            report_hash = next(
                item["sha256"]
                for item in audit_manifest["artifacts"]
                if item["id"] == "audit_report"
            )
            self.assertFalse((root / "PROJECT_CONTEXT.md").exists())
            _, context_manifest = fixture(root, context=True)
            self.assertEqual("context", validate_project(root)["profile"])
            self.assertEqual(
                report_hash,
                next(
                    item["sha256"]
                    for item in context_manifest["artifacts"]
                    if item["id"] == "audit_report"
                ),
            )
            self.assertEqual(
                "audit-20260922T120000Z-one", context_manifest["context_run_id"]
            )

    def test_section_provenance_hashes_all_covered_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, manifest = fixture(root, context=True)
            section = next(
                item for item in manifest["artifacts"] if item["id"] == "architecture"
            )["sections"][0]
            section["sources"] = []
            with self.assertRaisesRegex(ContractError, "hash every covered path"):
                validate_manifest(manifest)

    def test_context_document_preview_is_bounded_and_hosts_are_withheld(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, manifest = fixture(root, context=True)
            document = root / "repodocs/architecture.md"
            document.write_text("# Guide\n" + "x" * 70000)
            for artifact in manifest["artifacts"]:
                if artifact["id"] == "architecture":
                    artifact["sha256"] = sha256_text(document.read_text())
            write_json(root / "repodocs/project-context.manifest.json", manifest)
            nodes = dashboard_snapshot(root)["context_map"]["nodes"]
            architecture = next(item for item in nodes if item["id"] == "architecture")
            claude = next(item for item in nodes if item["id"] == "claude_host")
            self.assertLessEqual(len(architecture["preview"].encode()), 65536)
            self.assertTrue(architecture["preview_truncated"])
            self.assertEqual("", claude["preview"])

    def test_policy_preview_requires_classification_and_accepted_adr(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, candidate = base / "repo", base / "candidate"
            root.mkdir()
            fixture(root, context=True)
            policy = candidate / "repodocs/architecture.md"
            policy.parent.mkdir(parents=True)
            policy.write_text("# New guidance\n")
            change = {
                "path": "repodocs/architecture.md",
                "kind": "rule",
                "summary": "Require a boundary",
                "adr_id": "ADR-999",
            }
            pending = preview_context(root, candidate, {"changes": [change]})
            self.assertEqual("pending", pending["status"])
            self.assertIn("-# Guidance", pending["changes"][0]["diff"])
            change["adr_id"] = "ADR-001"
            self.assertEqual(
                "ready",
                preview_context(root, candidate, {"changes": [change]})["status"],
            )
            with self.assertRaisesRegex(ContractError, "classification"):
                preview_context(root, candidate, {"changes": []})
            (candidate / "repodocs/unowned.md").write_text("# No\n")
            with self.assertRaisesRegex(ContractError, "unowned"):
                preview_context(root, candidate, {"changes": [change]})

    def test_audit_only_and_greenfield_have_no_policy(self):
        for greenfield in (False, True):
            with (
                self.subTest(greenfield=greenfield),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                fixture(root, greenfield=greenfield)
                self.assertEqual("audit", validate_project(root)["profile"])
                snapshot = dashboard_snapshot(root)
                self.assertEqual("audit-only", snapshot["context"]["state"])
                self.assertEqual("complete", snapshot["audit"]["latest"]["outcome"])
                self.assertFalse((root / "PROJECT_CONTEXT.md").exists())
                if greenfield:
                    self.assertEqual([], snapshot["findings"])

    def test_task_brief_revisits_due_decision_without_promoting_audit_findings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root)
            self.assertIn(
                "not accepted rules", task_brief(root, "module responsibilities")
            )
            _, manifest = fixture(root, context=True)
            yesterday = (
                (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
            )
            decisions = root / "repodocs/decisions.md"
            decisions.write_text(
                decisions.read_text()
                + f'\n<a id="ADR-002"></a>\n## ADR-002: Revisit module\n- Status: deferred\n- Reason: Wait for callers\n- Review when: New caller appears\n- Review on: {yesterday}\n- Sources: architecture-001\n'
            )
            for artifact in manifest["artifacts"]:
                if artifact["id"] == "decisions":
                    artifact["sha256"] = sha256_text(decisions.read_text())
            write_json(root / "repodocs/project-context.manifest.json", manifest)
            self.assertTrue(
                dashboard_snapshot(root)["findings"][0]["deferred"][0]["review_due"]
            )
            brief = task_brief(root, "module responsibilities")
            self.assertIn("ADR-002: review when New caller appears (date due)", brief)

    def test_connected_context_survives_new_audit_and_dirty_source_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory, manifest = fixture(root, context=True)
            self.assertEqual("context", validate_project(root)["profile"])
            run = copy.deepcopy(inventory["runs"][0])
            run["id"] = "audit-20260922T130000Z-two"
            run["scanned_at"] = "2026-09-22T13:00:00Z"
            for result in run["results"].values():
                result["origin"] = "reused"
            inventory["runs"].append(run)
            report_path = f"repodocs/audit/reports/{run['id']}.md"
            (root / report_path).write_text("# New audit\n")
            manifest["artifacts"].append(
                {
                    "id": "audit_report_two",
                    "path": report_path,
                    "kind": "owned_file",
                    "sha256": sha256_text("# New audit\n"),
                }
            )
            write_json(root / "repodocs/audit/inventory.json", inventory)
            for item in manifest["artifacts"]:
                if item["id"] == "audit_inventory":
                    item["sha256"] = sha256_text((root / item["path"]).read_text())
            write_json(root / "repodocs/project-context.manifest.json", manifest)
            self.assertEqual(
                inventory["runs"][0]["id"], validate_project(root)["context_run_id"]
            )
            self.assertEqual(
                run["id"], dashboard_snapshot(root)["audit"]["latest"]["id"]
            )
            (root / "src/main.py").write_text("value = 2\n")
            report = drift(root)
            self.assertEqual(["src/main.py"], report["changed"])
            self.assertEqual("stale", report["context_status"])
            (root / "src/extra.py").write_text("value = 3\n")
            report = drift(root)
            self.assertIn("src/extra.py", report["added_unknown_impact"])
            self.assertEqual("unknown", report["context_status"])

    def test_empty_findings_scope_gap_and_deferred_date(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory, _ = fixture(root)
            inventory["runs"][0]["results"]["stack"]["scope"]["unscanned"] = [
                "src/main.py"
            ]
            with self.assertRaisesRegex(ContractError, "contradicts inventory"):
                validate_inventory(inventory)
            inventory["runs"][0]["results"]["stack"]["scope"]["unscanned"] = []
            next_run = copy.deepcopy(inventory["runs"][0])
            next_run["id"] = "audit-20260922T130000Z-two"
            for result in next_run["results"].values():
                result["origin"] = "reused"
            current = next_run["results"]["architecture"]
            current["origin"] = "current"
            current["source_run_id"] = next_run["id"]
            current["sources"][0]["sha256"] = sha256_bytes(b"changed")
            inventory["runs"].append(next_run)
            with self.assertRaisesRegex(ContractError, "disagree on source hash"):
                validate_inventory(inventory)
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        sections = _markdown_sections(
            f"## ADR-001: Later\n- Status: deferred\n- Reason: Wait\n- Review when: API available\n- Review on: {yesterday}\n",
            "ADR",
        )
        self.assertTrue(sections[0]["review_due"])

    def test_remediation_accepts_250_of_251_but_rejects_oversized_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory, manifest = fixture(root)
            findings_path = root / "repodocs/audit/findings/architecture.json"
            document = json.loads(findings_path.read_text())
            original = document["findings"][0]
            for number in range(2, 252):
                item = copy.deepcopy(original)
                item["id"] = f"architecture-{number:03d}"
                item["identity"]["assertion"] = f"mixed responsibilities {number}"
                document["findings"].append(item)
            write_json(findings_path, document)
            new_hash = sha256_text(findings_path.read_text())
            inventory["runs"][0]["results"]["architecture"]["sha256"] = new_hash
            write_json(root / "repodocs/audit/inventory.json", inventory)
            for artifact in manifest["artifacts"]:
                if artifact["id"] == "finding_architecture":
                    artifact["sha256"] = new_hash
                elif artifact["id"] == "audit_inventory":
                    artifact["sha256"] = sha256_text(
                        (root / artifact["path"]).read_text()
                    )
            write_json(root / "repodocs/project-context.manifest.json", manifest)
            snapshot = dashboard_snapshot(root)
            self.assertEqual(251, snapshot["finding_summary"]["active"])
            selected = [
                {
                    "id": item["id"],
                    "auditor": item["auditor"],
                    "status": item["status"],
                    "identity_sha256": item["identity_sha256"],
                }
                for item in snapshot["findings"]
            ]
            binding = {
                "repository": {
                    "snapshot_id": snapshot["snapshot_id"],
                    "audit_run_id": inventory["runs"][0]["id"],
                },
                "expected_active_count": 251,
                "expected_active_sha256": snapshot["finding_summary"][
                    "active_set_sha256"
                ],
                "selected_findings": selected[:250],
            }
            self.assertEqual(250, validate_remediation(root, binding)["selected"])
            binding["selected_findings"] = selected
            with self.assertRaisesRegex(ContractError, "250"):
                validate_remediation(root, binding)
            binding["selected_findings"] = selected[:1]
            binding["expected_active_sha256"] = sha256_bytes(b"wrong")
            with self.assertRaisesRegex(ContractError, "complete active"):
                validate_remediation(root, binding)

    def test_archive_legacy_preserves_cited_id_binding_without_host_file(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "repo"
            root.mkdir()
            _, manifest = fixture(root, context=True)
            manifest["schema_version"] = 1
            for key in ("profile", "context_run_id", "reserved_ids"):
                del manifest[key]
            for artifact in manifest["artifacts"]:
                artifact.pop("sections", None)
            write_json(root / "repodocs/project-context.manifest.json", manifest)
            (root / "src/main.py").write_text("value = 1  # ADR-001\n")
            subprocess.run(["git", "-C", str(root), "add", "src/main.py"], check=True)
            result = archive_legacy(root, base / "archive")
            self.assertEqual("archived", result["status"])
            self.assertEqual("ADR-001", result["reserved_ids"][0]["id"])
            self.assertFalse((base / "archive/CLAUDE.md").exists())
            self.assertTrue((base / "archive/repodocs/decisions.md").exists())
