#!/usr/bin/env python3
"""Write, validate and (optionally) commit + tag a release manifest. Run from the repo root.

Full release (every image built for this version):
  publish_release.py --product mta --version 2.4.0 \
      --image mta=sdmainacr.azurecr.io/mta:2.4.0@sha256:<digest> --revision mta=<40-hex sha> [--notes FILE]

Partial release (e.g. worker-only hotfix): rebuild some images, carry the rest over from the base release:
  publish_release.py --product dotnet-app --version 1.4.1 --base-release 1.4.0 --base-manifest base.json \
      --image worker=sdmainacr.azurecr.io/dotnet-app-worker:1.4.1@sha256:<digest> --revision worker=<sha> \
      --carry server --carry react

dotnet-app is written as schema version 2 and also needs --rank, --database-changes and --downtime-minutes
(optional: --security, --highlight up to three times, --migrations <classification.json>):
  publish_release.py --product dotnet-app --version 8.6.0 ... --rank minor --database-changes additive \
      --downtime-minutes 15 --highlight "Faster classification" --migrations classification.json
mta and solr are written as schema version 1 and refuse those options.

Rules enforced here: at least one image is built for this version (tag == version); carried images come
verbatim from the base manifest; version > base release. Without --push nothing is committed, tagged or
pushed (dry run: files only). The tag is created last, after the commit is pushed. Never overwrites.
"""
import argparse, datetime, json, pathlib, re, subprocess, sys

SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?$")

def run(*a): subprocess.run(a, check=True)
def core(v): return tuple(int(x) for x in v.split("-")[0].split("."))

def order_key(v):
    """SemVer order: the three numbers; a pre-release is older than the plain version; identifiers
    compare numerically when both are numbers (so beta.2 < beta.10), numbers before text."""
    pre = v.split("-", 1)[1] if "-" in v else None
    ids = () if pre is None else tuple((0, int(x), "") if x.isdigit() else (1, 0, x) for x in pre.split("."))
    return core(v), pre is None, ids

V2_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-beta\.[1-9]\d*)?$")
V2_OPTIONS = ("rank", "security", "database_changes", "downtime_minutes", "highlight", "migrations")

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
    ap.add_argument("--rank", choices=["patch", "minor", "major"], help="dotnet-app: size of the release")
    ap.add_argument("--security", action="store_true", help="dotnet-app: the release carries a security fix")
    ap.add_argument("--database-changes", choices=["none", "additive", "breaking"], help="dotnet-app: classification of the release's migrations")
    ap.add_argument("--downtime-minutes", type=int, help="dotnet-app: expected downtime, 1 to 240")
    ap.add_argument("--highlight", action="append", default=[], help="dotnet-app: a customer-facing highlight (at most 3, each at most 80 characters)")
    ap.add_argument("--migrations", help="dotnet-app: classification.json written by the release classifier")
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()

    if not SEMVER.match(a.version): sys.exit(f"ERROR: version {a.version!r} is not SemVer")
    v2 = a.product == "dotnet-app"
    if v2:
        if not V2_VERSION.match(a.version): sys.exit(f"ERROR: dotnet-app version {a.version!r} must be X.Y.Z or X.Y.Z-beta.N")
        for opt, flag in (("rank", "--rank"), ("database_changes", "--database-changes"), ("downtime_minutes", "--downtime-minutes")):
            if getattr(a, opt) is None: sys.exit(f"ERROR: {flag} is required for dotnet-app")
        if not 1 <= a.downtime_minutes <= 240: sys.exit("ERROR: --downtime-minutes must be 1 to 240")
        if len(a.highlight) > 3: sys.exit("ERROR: at most 3 --highlight values")
        for h in a.highlight:
            if not 1 <= len(h) <= 80: sys.exit(f"ERROR: a highlight must be 1 to 80 characters: {h!r}")
        migrations = []
        if a.migrations:
            c = json.load(open(a.migrations))
            if c.get("databaseChanges") != a.database_changes:
                sys.exit(f"ERROR: --database-changes {a.database_changes} disagrees with {a.migrations} ({c.get('databaseChanges')!r})")
            migrations = c.get("migrations", [])
        # The sign-off's record must agree with the classification: none has no migrations, anything else names them.
        if a.database_changes == "none" and migrations: sys.exit("ERROR: --database-changes none, but the classification lists migrations")
        if a.database_changes != "none" and not migrations:
            sys.exit(f"ERROR: --database-changes {a.database_changes} needs --migrations <classification.json> naming at least one migration")
    else:
        used = [o for o in V2_OPTIONS if getattr(a, o) not in (None, False, [])]
        if used: sys.exit(f"ERROR: {', '.join('--' + o.replace('_', '-') for o in used)} apply to dotnet-app only")
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
        if not order_key(a.version) > order_key(a.base_release):
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

    manifest = dict(schemaVersion=2 if v2 else 1, product=a.product, version=a.version,
        releasedAt=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **manifest_base, images=dict(sorted(images.items())), releaseNotes=str(md))
    if v2:
        manifest.update(rank=a.rank, security=a.security, databaseChanges=a.database_changes, migrations=migrations,
                        downtimeMinutes=a.downtime_minutes, highlights=a.highlight, guides=[])
    import jsonschema  # pip install jsonschema
    schema_file = "schema/release-manifest.v2.schema.json" if v2 else "schema/release-manifest.v1.schema.json"
    jsonschema.validate(manifest, json.load(open(schema_file)))  # before writing anything
    base.mkdir(parents=True, exist_ok=True)
    mf.write_text(json.dumps(manifest, indent=2) + "\n")
    default_notes = f"# {a.product} {a.version}\n\n" + "\n".join(f"- {r}: `{i['tag']}` from `{i['sourceRevision']}`" for r, i in sorted(images.items())) + "\n"
    if a.notes and v2:  # the customer's text first, the component lines last under Technical
        components = "\n".join(f"- {r}: `{i['tag']}` from `{i['sourceRevision']}`" for r, i in sorted(images.items())) + "\n"
        md.write_text(pathlib.Path(a.notes).read_text().rstrip("\n") + "\n\n## Technical\n\n" + components)
    else:
        md.write_text(pathlib.Path(a.notes).read_text() if a.notes else default_notes)
    print(f"Wrote {mf} (schema-valid)")
    if not a.push: print("Dry run: not committed, tagged or pushed."); return

    run("git", "add", str(mf), str(md))
    run("git", "commit", "-m", f"Release {a.product} {a.version}")
    run("git", "push", "origin", "HEAD:main")
    run("git", "tag", "-a", tag, "-m", f"{a.product} {a.version}")   # tag last
    run("git", "push", "origin", tag)

if __name__ == "__main__": main()
