"""Pinned artifact verification, private registration and export; synthetic artifacts only."""

import hashlib
import io
import json
import os
import stat
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_workflow import SOURCE, GitFixture, install, workflow

# isort: split
import install_tool
import review_cli

A1_MODULES = (
    "ci_evidence",
    "copilot_policy",
    "github_transport",
    "install_tool",
    "profiles",
    "review_cli",
    "review_policy",
)


class ReviewerInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary = self.root / "claude"
        self.binary.write_bytes(b"synthetic executable, never run")
        spec = dict(review_cli.PROVIDERS["claude-code"]["cli"])
        spec["binary_sha256"] = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(
            json.dumps(
                {"version": "2.1.282", "platforms": {"linux-x64": {"checksum": spec["binary_sha256"]}}}
            )
        )
        spec["manifest_sha256"] = hashlib.sha256(self.manifest.read_bytes()).hexdigest()
        self.spec = spec
        self.signature = self.root / "manifest.json.sig"
        self.signature.write_bytes(b"synthetic signature")
        self.key = self.root / "key.asc"
        self.key.write_bytes(b"synthetic public key")

    def verify(self, status=None):
        def run(args, **kwargs):
            self.assertIn("--no-options", args)
            home = Path(args[args.index("--homedir") + 1])
            self.assertNotEqual(home, Path.home())
            self.assertEqual(home.stat().st_mode & 0o777, 0o700)
            self.assertEqual(kwargs["env"]["HOME"], str(home))
            self.assertNotIn("GNUPGHOME", kwargs["env"])
            return subprocess.CompletedProcess(
                args,
                0,
                status
                if status is not None
                else b"[GNUPG:] VALIDSIG 31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE fixture\n",
                b"",
            )

        with (
            patch.dict(review_cli.PROVIDERS["claude-code"], cli=self.spec),
            patch.object(review_cli, "supported_platform"),
            patch.object(review_cli.shutil, "which", return_value="/usr/bin/gpg"),
            patch.object(review_cli.subprocess, "run", side_effect=run),
        ):
            return review_cli.verify_claude(self.binary, self.manifest, self.signature, self.key)

    def test_signed_pinned_identity_returns_absolute_binary(self):
        self.assertEqual(self.verify(), str(self.binary))

    def test_wrong_signature_key_or_ambiguous_signatures_are_rejected(self):
        for value in [
            b"",
            b"[GNUPG:] VALIDSIG wrong-key fixture\n",
            b"[GNUPG:] VALIDSIG 31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE fixture\n" * 2,
        ]:
            with self.subTest(status=value[:20]), self.assertRaises(workflow.WorkflowError):
                self.verify(value)

    def test_tampered_binary_manifest_and_symlink_are_rejected(self):
        self.binary.write_bytes(b"changed executable")
        with self.assertRaisesRegex(workflow.WorkflowError, "digest"):
            self.verify()
        self.binary.unlink()
        self.binary.symlink_to(self.key)
        with self.assertRaises(workflow.WorkflowError):
            self.verify()
        self.binary.unlink()
        self.binary.write_bytes(b"synthetic executable, never run")
        self.manifest.write_text("{}")
        with self.assertRaisesRegex(workflow.WorkflowError, "digest"):
            self.verify()

    def test_unknown_platform_is_refused(self):
        with patch.object(review_cli.platform, "system", return_value="Darwin"):
            with self.assertRaises(workflow.WorkflowError):
                review_cli.supported_platform()

    def test_install_refuses_existing_destination_without_network_or_replacement(self):
        for provider, filename in [("claude-code", "claude"), ("copilot", "copilot")]:
            folder = self.root / provider
            folder.mkdir()
            existing = folder / filename
            existing.write_bytes(b"user installation")
            with (
                patch.object(review_cli, "supported_platform"),
                patch.object(install_tool, "download") as download,
            ):
                with self.assertRaisesRegex(workflow.WorkflowError, "Destination exists"):
                    install_tool.install(provider, folder)
                download.assert_not_called()
            self.assertEqual(existing.read_bytes(), b"user installation")

    def test_install_stages_artifacts_privately_under_a_permissive_umask(self):
        binary = self.root / "member"
        binary.write_bytes(b"fixture copilot")
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
            member = tarfile.TarInfo("copilot")
            member.size = len(binary.read_bytes())
            bundle.addfile(member, io.BytesIO(binary.read_bytes()))
        data = buffer.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        folder = self.root / "install"
        previous = os.umask(0o002)
        try:
            with (
                patch.object(review_cli, "supported_platform"),
                patch.object(install_tool, "download", return_value=data),
                patch.object(install_tool, "CLI_ARCHIVE_SHA256", digest),
                patch.dict(
                    review_cli.PROVIDERS["copilot"],
                    cli={**review_cli.PROVIDERS["copilot"]["cli"], "archive_sha256": digest},
                ),
            ):
                result = install_tool.install("copilot", folder)
        finally:
            os.umask(previous)
        self.assertEqual(result, str(folder / "copilot"))
        self.assertEqual(stat.S_IMODE((folder / "copilot").stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((folder / "copilot.tar.gz").stat().st_mode), 0o644)
        self.assertEqual((folder / "copilot").read_bytes(), b"fixture copilot")

    def test_hosted_route_remains_explicit_copilot_and_disabled_by_default(self):
        text = (SOURCE / ".github/workflows/copilot-review.yml").read_text()
        self.assertIn("AGENTIC_COPILOT_ACTIONS_ENABLED == 'true'", text)
        self.assertIn("--require-reviewer-backend copilot", text)
        self.assertIn("register-reviewer copilot", text)
        self.assertIn('get("hosted_profile")', text)
        self.assertIn("review.py qualify", text)
        self.assertIn("always() && inputs.publish", text)
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", text)
        self.assertNotIn("claude-review-token", text)

    def test_copilot_binary_is_checked_against_pinned_archive_member(self):
        binary = self.root / "copilot"
        binary.write_bytes(b"fixture copilot")
        archive = self.root / "copilot.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            member = tarfile.TarInfo("copilot")
            member.size = len(binary.read_bytes())
            bundle.addfile(member, io.BytesIO(binary.read_bytes()))
        spec = {
            **review_cli.PROVIDERS["copilot"]["cli"],
            "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        }
        with (
            patch.dict(review_cli.PROVIDERS["copilot"], cli=spec),
            patch.object(review_cli, "supported_platform"),
        ):
            self.assertEqual(review_cli.verify_copilot(binary, archive), str(binary))
            binary.write_bytes(b"tampered")
            with self.assertRaises(workflow.WorkflowError):
                review_cli.verify_copilot(binary, archive)

    def test_export_contains_the_review_foundation_but_no_private_state(self):
        target = self.root / "export"
        result = install.install(SOURCE, target, apply=True)
        self.assertTrue(result["applied"])
        for name in A1_MODULES:
            self.assertTrue((target / "scripts/agentic" / (name + ".py")).is_file(), name)
        self.assertTrue((target / "tests/agentic/fixtures/config-flowdc-schema2.json").is_file())
        self.assertFalse((target / ".agentic-local").exists())
        self.assertFalse(list(target.rglob("*review-token*")))


class ReviewerRegistrationTests(GitFixture):
    def synthetic_copilot(self):
        proof = self.parent / "proof"
        proof.mkdir()
        binary = proof / "copilot"
        binary.write_bytes(b"fixture copilot")
        archive = proof / "copilot.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            member = tarfile.TarInfo("copilot")
            member.size = len(binary.read_bytes())
            bundle.addfile(member, io.BytesIO(binary.read_bytes()))
        spec = {
            **review_cli.PROVIDERS["copilot"]["cli"],
            "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        }
        return binary, proof, spec

    def test_register_copilot_creates_a_private_verified_bundle(self):
        binary, proof, spec = self.synthetic_copilot()
        with (
            patch.dict(review_cli.PROVIDERS["copilot"], cli=spec),
            patch.object(review_cli, "supported_platform"),
        ):
            with self.assertRaisesRegex(workflow.WorkflowError, "register"):
                review_cli.executable(self.repo, "copilot")
            first = review_cli.register(self.repo, "copilot", binary, proof)
            self.assertEqual(first, {"provider": "copilot", "registered": True, "existing": False})
            bundle = self.root / ".agentic-local/provider-clis/copilot" / spec["version"]
            self.assertEqual(stat.S_IMODE(bundle.stat().st_mode), 0o700)
            self.assertEqual(review_cli.executable(self.repo, "copilot"), str(bundle / "copilot"))
            second = review_cli.register(self.repo, "copilot", binary, proof)
            self.assertTrue(second["existing"])
            (bundle / "copilot").write_bytes(b"tampered after registration")
            with self.assertRaises(workflow.WorkflowError):
                review_cli.executable(self.repo, "copilot")
        self.assertEqual(workflow.run(["git", "-C", self.root, "status", "--porcelain"]).stdout, "")

    def test_register_refuses_unverified_binaries_and_task_checkouts(self):
        binary, proof, spec = self.synthetic_copilot()
        binary.write_bytes(b"not the archive member")
        with (
            patch.dict(review_cli.PROVIDERS["copilot"], cli=spec),
            patch.object(review_cli, "supported_platform"),
        ):
            with self.assertRaises(workflow.WorkflowError):
                review_cli.register(self.repo, "copilot", binary, proof)
        self.assertFalse((self.root / ".agentic-local/provider-clis").exists())
        with self.assertRaisesRegex(workflow.WorkflowError, "Unsupported reviewer provider"):
            review_cli.register(self.repo, "other", binary, proof)
