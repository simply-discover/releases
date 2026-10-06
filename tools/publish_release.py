#!/usr/bin/env python3
"""Write, validate and (optionally) commit + tag a release manifest. Run from the repo root.

Full release (every image built for this version):
  publish_release.py --product mta --version 2.4.0 \
      --image mta=sdmainacr.azurecr.io/mta:2.4.0@sha256:<digest> --revision mta=<40-hex sha> [--notes FILE]

Partial release (e.g. worker-only hotfix): rebuild some images, carry the rest over from the base release:
  publish_release.py --product dotnet-app --version 1.4.1 --base-release 1.4.0 --base-manifest base.json \
      --image worker=sdmainacr.azurecr.io/dotnet-app-worker:1.4.1@sha256:<digest> --revision worker=<sha> \
      --carry server --carry react

Rules enforced here: at least one image is built for this version (tag == version); carried images come
verbatim from the base manifest; version > base release. Without --push nothing is committed, tagged or
pushed (dry run: files only). The tag is created last, after the commit is pushed. Never overwrites.
"""
import argparse, datetime, json, pathlib, re, subprocess, sys

SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?$")

def run(*a): subprocess.run(a, check=True)
def core(v): return tuple(int(x) for x in v.split("-")[0].split("."))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--image", action="append", default=[], help="role=registry/repo:tag@sha256:digest (built for this version)")
    ap.add_argument("--revision", action="append", default=[], help="role=<40-hex commit> for each --image")
    ap.add_argument("--carry", action="append", default=[], help="role to carry over unchanged from --base-manifest")
    ap.add_argument("--base-release", help="version of the release the carried images come from")
    ap.add_argument("--base-manifest", help="path to the base release's manifest JSON")
    ap.add_argument("--notes", help="markdown file for release notes")
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()

    if not SEMVER.match(a.version): sys.exit(f"ERROR: version {a.version!r} is not SemVer")
    tag = f"{a.product}/{a.version}"
    base = pathlib.Path(a.product) / "versions"
    mf, md = base / f"{a.version}.json", base / f"{a.version}.md"
    if mf.exists():
        sys.exit(f"ERROR: {mf} already exists; releases are immutable")
    # git may be absent (e.g. slim CI container). A dry run then relies on the caller's own tag
    # check; --push needs git anyway, so it fails closed.
    try:
        if subprocess.run(["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}"], capture_output=True).returncode == 0:
            sys.exit(f"ERROR: {tag} already exists; releases are immutable")
    except FileNotFoundError:
        if a.push: sys.exit("ERROR: git is required with --push")
        print("WARNING: git not found; skipped local tag-exists check")

    revisions = dict(r.split("=", 1) for r in a.revision)
    images = {}
    for spec in a.image:
        role, ref = spec.split("=", 1)
        m = re.fullmatch(r"([^/]+)/([^:@]+):([^@]+)@(sha256:[0-9a-f]{64})", ref)
        if not m: sys.exit(f"ERROR: bad --image {spec!r}")
        if m[3] != a.version: sys.exit(f"ERROR: built image {role} has tag {m[3]}, expected {a.version}")
        if not re.fullmatch(r"[0-9a-f]{40}", revisions.get(role, "")): sys.exit(f"ERROR: --revision {role}=<40-hex sha> required")
        images[role] = dict(registry=m[1], repository=m[2], tag=m[3], digest=m[4], sourceRevision=revisions[role])
    extra = set(revisions) - set(images)
    if extra: sys.exit(f"ERROR: --revision given for non-built role(s): {sorted(extra)}")
    if not images: sys.exit("ERROR: at least one --image must be built for this version")

    manifest_base = {}
    if a.carry or a.base_release or a.base_manifest:
        if not (a.carry and a.base_release and a.base_manifest):
            sys.exit("ERROR: --carry, --base-release and --base-manifest go together")
        if not core(a.version) > core(a.base_release):
            sys.exit(f"ERROR: version {a.version} must be greater than base release {a.base_release}")
        b = json.load(open(a.base_manifest))
        if b["product"] != a.product or b["version"] != a.base_release:
            sys.exit("ERROR: base manifest does not match --product/--base-release")
        for role in a.carry:
            if role in images: sys.exit(f"ERROR: {role} is both built and carried")
            if role not in b["images"]: sys.exit(f"ERROR: base release has no image {role!r}")
            images[role] = b["images"][role]
        # The carried set plus the built set must be exactly the base release's roles.
        if set(images) != set(b["images"]):
            sys.exit(f"ERROR: roles {sorted(images)} do not match the base release's {sorted(b['images'])}")
        manifest_base = {"baseRelease": a.base_release}

    manifest = dict(schemaVersion=1, product=a.product, version=a.version,
        releasedAt=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **manifest_base, images=dict(sorted(images.items())), releaseNotes=str(md))
    import jsonschema  # pip install jsonschema
    jsonschema.validate(manifest, json.load(open("schema/release-manifest.v1.schema.json")))  # before writing anything
    base.mkdir(parents=True, exist_ok=True)
    mf.write_text(json.dumps(manifest, indent=2) + "\n")
    default_notes = f"# {a.product} {a.version}\n\n" + "\n".join(f"- {r}: `{i['tag']}` from `{i['sourceRevision']}`" for r, i in sorted(images.items())) + "\n"
    md.write_text(pathlib.Path(a.notes).read_text() if a.notes else default_notes)
    print(f"Wrote {mf} (schema-valid)")
    if not a.push: print("Dry run: not committed, tagged or pushed."); return

    run("git", "add", str(mf), str(md))
    run("git", "commit", "-m", f"Release {a.product} {a.version}")
    run("git", "push", "origin", "HEAD:main")
    run("git", "tag", "-a", tag, "-m", f"{a.product} {a.version}")   # tag last
    run("git", "push", "origin", tag)

if __name__ == "__main__": main()
