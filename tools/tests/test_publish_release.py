"""Tests for tools/publish_release.py. Each test runs the tool as a dry run (never --push) in a
temporary copy of schema/ and tools/, so the real tree is never written."""
import glob, json, pathlib, shutil, subprocess, sys, tempfile, unittest

import jsonschema

REPO = pathlib.Path(__file__).resolve().parents[2]
SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64
OLD_DIGEST = "sha256:" + "c" * 64


def image(role, repo, tag, digest=DIGEST):
    return f"{role}=sdmainacr.azurecr.io/{repo}:{tag}@{digest}"


class PublishTool(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        shutil.copytree(REPO / "schema", self.tmp / "schema")
        shutil.copytree(REPO / "tools", self.tmp / "tools", ignore=shutil.ignore_patterns("tests", "__pycache__"))

    def run_tool(self, *args):
        assert "--push" not in args
        return subprocess.run([sys.executable, str(self.tmp / "tools" / "publish_release.py"), *args],
                              cwd=self.tmp, capture_output=True, text=True)

    def written(self):
        return sorted(str(p.relative_to(self.tmp)) for p in self.tmp.glob("*/versions/*"))

    def dotnet_args(self, version="8.6.0", **skip):
        args = ["--product", "dotnet-app", "--version", version]
        for role, repo in (("server", "dotnet-app-server"), ("worker", "dotnet-app-worker"), ("react", "react-app")):
            args += ["--image", image(role, repo, version), "--revision", f"{role}={SHA}"]
        opts = {"--rank": "minor", "--database-changes": "none", "--downtime-minutes": "15"}
        for k in skip.get("drop", ()):
            opts.pop(k)
        for k, v in opts.items():
            args += [k, v]
        return args

    def classification(self, database_changes="additive"):
        path = self.tmp / "classification.json"
        path.write_text(json.dumps({"databaseChanges": database_changes, "migrations": [
            {"id": "20261124101500_AddCaseNotes", "impact": "additive", "reason": "adds a table"}]}))
        return str(path)

    def manifest(self, version="8.6.0", product="dotnet-app"):
        return json.load(open(self.tmp / product / "versions" / f"{version}.json"))

    def test_full_dotnet_release_writes_valid_v2(self):
        r = self.run_tool(*self.dotnet_args(), "--security", "--database-changes", "additive",
                          "--migrations", self.classification(), "--highlight", "Export filters", "--highlight", "12 fixes")
        self.assertEqual(r.returncode, 0, r.stderr)
        m = self.manifest()
        jsonschema.validate(m, json.load(open(REPO / "schema/release-manifest.v2.schema.json")))
        self.assertEqual((m["schemaVersion"], m["rank"], m["security"], m["databaseChanges"], m["downtimeMinutes"]),
                         (2, "minor", True, "additive", 15))
        self.assertEqual(m["highlights"], ["Export filters", "12 fixes"])
        self.assertEqual(m["guides"], [])
        self.assertEqual(m["migrations"][0]["id"], "20261124101500_AddCaseNotes")
        self.assertNotIn("channel", m)

    def test_security_defaults_to_false_and_migrations_to_empty(self):
        self.assertEqual(self.run_tool(*self.dotnet_args()).returncode, 0)
        m = self.manifest()
        self.assertIs(m["security"], False)
        self.assertEqual((m["migrations"], m["highlights"]), ([], []))

    def test_each_missing_required_option_is_refused_and_nothing_written(self):
        for opt in ("--rank", "--database-changes", "--downtime-minutes"):
            with self.subTest(opt):
                r = self.run_tool(*self.dotnet_args(drop=[opt]))
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(opt, r.stderr)
                self.assertEqual(self.written(), [])

    def test_four_highlights_refused(self):
        r = self.run_tool(*self.dotnet_args(), *sum((["--highlight", h] for h in "abcd"), []))
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.written(), [])

    def test_highlight_over_80_characters_refused(self):
        r = self.run_tool(*self.dotnet_args(), "--highlight", "x" * 81)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.written(), [])
        self.assertEqual(self.run_tool(*self.dotnet_args(), "--highlight", "x" * 80).returncode, 0)

    def test_downtime_out_of_range_refused(self):
        for bad in ("0", "241"):
            args = self.dotnet_args()
            args[args.index("--downtime-minutes") + 1] = bad
            self.assertNotEqual(self.run_tool(*args).returncode, 0)
        self.assertEqual(self.written(), [])

    def test_database_changes_disagreeing_with_classification_refused(self):
        args = self.dotnet_args()
        args[args.index("--database-changes") + 1] = "breaking"
        r = self.run_tool(*args, "--migrations", self.classification("additive"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("disagrees", r.stderr)
        self.assertEqual(self.written(), [])

    def test_database_changes_without_its_migrations_refused_and_nothing_written(self):
        # additive or breaking with no classification file: there would be no record of what changed
        for changes in ("additive", "breaking"):
            r = self.run_tool(*self.dotnet_args(), "--database-changes", changes)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("--migrations", r.stderr)
            self.assertEqual(self.written(), [])

    def test_none_with_a_classification_that_lists_migrations_refused(self):
        path = self.tmp / "classification.json"
        path.write_text(json.dumps({"databaseChanges": "none", "migrations": [
            {"id": "20261124101500_AddCaseNotes", "impact": "additive", "reason": "adds a table"}]}))
        r = self.run_tool(*self.dotnet_args(), "--migrations", str(path))
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.written(), [])

    def test_beta_accepted_and_rc_refused_for_dotnet_app(self):
        self.assertEqual(self.run_tool(*self.dotnet_args("8.6.0-beta.2")).returncode, 0)
        self.assertEqual(self.manifest("8.6.0-beta.2")["version"], "8.6.0-beta.2")
        r = self.run_tool(*self.dotnet_args("8.6.0-rc.1"))
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.tmp / "dotnet-app/versions/8.6.0-rc.1.json").exists())

    def test_stable_after_its_beta_is_newer(self):
        base = {"schemaVersion": 2, "product": "dotnet-app", "version": "8.6.0-beta.2",
                "images": {r: {"registry": "sdmainacr.azurecr.io", "repository": f"x-{r}", "tag": "8.6.0-beta.2",
                               "digest": OLD_DIGEST, "sourceRevision": SHA} for r in ("server", "worker", "react")}}
        (self.tmp / "base.json").write_text(json.dumps(base))
        args = ["--product", "dotnet-app", "--version", "8.6.0", "--base-release", "8.6.0-beta.2", "--base-manifest", "base.json",
                "--image", image("worker", "dotnet-app-worker", "8.6.0"), "--revision", f"worker={SHA}",
                "--carry", "server", "--carry", "react", "--rank", "minor", "--database-changes", "none", "--downtime-minutes", "15"]
        self.assertEqual(self.run_tool(*args).returncode, 0)

    def test_partial_release_keeps_carried_tag_and_records_base(self):
        base = {"schemaVersion": 2, "product": "dotnet-app", "version": "8.6.0",
                "images": {r: {"registry": "sdmainacr.azurecr.io", "repository": f"x-{r}", "tag": "8.6.0",
                               "digest": OLD_DIGEST, "sourceRevision": SHA} for r in ("server", "worker", "react")}}
        (self.tmp / "base.json").write_text(json.dumps(base))
        args = ["--product", "dotnet-app", "--version", "8.6.1", "--base-release", "8.6.0", "--base-manifest", "base.json",
                "--image", image("worker", "dotnet-app-worker", "8.6.1"), "--revision", f"worker={SHA}",
                "--carry", "server", "--carry", "react", "--rank", "patch", "--database-changes", "none", "--downtime-minutes", "5"]
        r = self.run_tool(*args)
        self.assertEqual(r.returncode, 0, r.stderr)
        m = self.manifest("8.6.1")
        self.assertEqual(m["baseRelease"], "8.6.0")
        self.assertEqual((m["images"]["server"]["tag"], m["images"]["react"]["tag"], m["images"]["worker"]["tag"]),
                         ("8.6.0", "8.6.0", "8.6.1"))
        self.assertEqual(m["images"]["server"]["digest"], OLD_DIGEST)

    def test_mta_still_writes_version_1_and_refuses_new_options(self):
        mta = ["--product", "mta", "--version", "2.8.0", "--image", image("mta", "mta", "2.8.0"), "--revision", f"mta={SHA}"]
        r = self.run_tool(*mta, "--rank", "minor")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.written(), [])
        for opt in (["--security"], ["--highlight", "x"], ["--downtime-minutes", "5"], ["--database-changes", "none"]):
            self.assertNotEqual(self.run_tool(*mta, *opt).returncode, 0, opt)
        self.assertEqual(self.run_tool(*mta).returncode, 0)
        m = self.manifest("2.8.0", "mta")
        self.assertEqual(m["schemaVersion"], 1)
        self.assertNotIn("rank", m)
        jsonschema.validate(m, json.load(open(REPO / "schema/release-manifest.v1.schema.json")))

    def test_notes_with_and_without_notes_option(self):
        self.assertEqual(self.run_tool(*self.dotnet_args("8.6.0")).returncode, 0)
        plain = (self.tmp / "dotnet-app/versions/8.6.0.md").read_text()
        self.assertTrue(plain.startswith("# dotnet-app 8.6.0\n\n- react: `8.6.0` from `"))
        self.assertNotIn("## Technical", plain)

        notes = self.tmp / "notes.md"
        notes.write_text("### New\n- Export filters\n")
        self.assertEqual(self.run_tool(*self.dotnet_args("8.6.1"), "--notes", str(notes)).returncode, 0)
        text = (self.tmp / "dotnet-app/versions/8.6.1.md").read_text()
        self.assertTrue(text.startswith("### New\n- Export filters\n\n## Technical\n"))
        self.assertTrue(text.rstrip().endswith(f"- worker: `8.6.1` from `{SHA}`"))
        self.assertLess(text.index("Export filters"), text.index("- react:"))

    def test_existing_manifests_validate_against_version_1(self):
        schema = json.load(open(REPO / "schema/release-manifest.v1.schema.json"))
        files = glob.glob(str(REPO / "*/versions/*.json"))
        self.assertTrue(files)
        for f in files:
            m = json.load(open(f))
            if m["schemaVersion"] == 1:
                jsonschema.validate(m, schema)


if __name__ == "__main__":
    unittest.main()
