#!/usr/bin/env python3
"""Write, validate and tag a release manifest. Run from the repo root.

Usage: publish_release.py --product mta --version 2.4.0 \
         --source-repo simply-discover/mta-robin --source-revision <sha> \
         --image mta=sdmainacr.azurecr.io/mta:2.4.0@sha256:<digest> [--notes FILE] [--push]

Without --push nothing is committed, tagged or pushed (dry run: files only).
The tag is created last, after the commit is pushed. Refuses to overwrite.
"""
import argparse, datetime, json, pathlib, re, subprocess, sys

def run(*a): subprocess.run(a, check=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--source-repo", required=True)
    ap.add_argument("--source-revision", required=True)
    ap.add_argument("--image", action="append", required=True, help="role=registry/repo:tag@sha256:digest")
    ap.add_argument("--notes", help="markdown file for release notes")
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()

    tag = f"{a.product}/{a.version}"
    base = pathlib.Path(a.product) / "versions"
    mf, md = base / f"{a.version}.json", base / f"{a.version}.md"
    if mf.exists() or subprocess.run(["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}"], capture_output=True).returncode == 0:
        sys.exit(f"ERROR: {tag} already exists; releases are immutable")

    images = {}
    for spec in a.image:
        role, ref = spec.split("=", 1)
        m = re.fullmatch(r"([^/]+)/([^:@]+):([^@]+)@(sha256:[0-9a-f]{64})", ref)
        if not m: sys.exit(f"ERROR: bad --image {spec!r}")
        images[role] = dict(registry=m[1], repository=m[2], tag=m[3], digest=m[4])
        if m[3] != a.version: sys.exit(f"ERROR: image tag {m[3]} != version {a.version}")

    manifest = dict(schemaVersion=1, product=a.product, version=a.version,
        releasedAt=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        sourceRevision=a.source_revision, sourceRepository=a.source_repo,
        images=images, releaseNotes=str(md))
    import jsonschema  # pip install jsonschema
    jsonschema.validate(manifest, json.load(open("schema/release-manifest.v1.schema.json")))  # before writing anything
    base.mkdir(parents=True, exist_ok=True)
    mf.write_text(json.dumps(manifest, indent=2) + "\n")
    md.write_text(pathlib.Path(a.notes).read_text() if a.notes else f"# {a.product} {a.version}\n\nSource revision `{a.source_revision}`.\n")

    print(f"Wrote {mf} (schema-valid)")
    if not a.push: print("Dry run: not committed, tagged or pushed."); return

    run("git", "add", str(mf), str(md))
    run("git", "commit", "-m", f"Release {a.product} {a.version}")
    run("git", "push", "origin", "HEAD:main")
    run("git", "tag", "-a", tag, "-m", f"{a.product} {a.version}")   # tag last
    run("git", "push", "origin", tag)

if __name__ == "__main__": main()
