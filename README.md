# Simply Discover Releases

Metadata-only catalogue of Simply Discover releases. It holds no source code,
images or credentials.

## Layout

One directory per product/deployment (`mta`, `solr`, `dotnet-app`, ...):

```text
├── schema/release-manifest.v1.schema.json
├── tools/                      publish + validation helpers
└── <product>/versions/
    ├── <version>.json          release manifest (validated against the schema)
    └── <version>.md            customer-facing release notes
```

## Rules

- A release is one SemVer version (e.g. `2.4.0`) of one product, shared by the
  Git tag, the manifest, the image tags and the release notes.
- Git tags are `<product>/<version>` (e.g. `mta/2.4.0`) so products version
  independently in this one repo.
- Manifests record image digests read back from `sdmainacr.azurecr.io`, never
  build-time guesses.
- The tag is created **last**, only after every image is pushed and its digest
  verified. The tag is the Renovate discovery signal.
- Published tags and manifests are immutable. A faulty release is withdrawn via
  `supportStatus` / `supersededBy` in a new commit, or replaced by a new version.
- Consumers must fetch manifests by tag, never from `main`.

## Client usage (Renovate)

```yaml
# renovate: datasource=github-tags depName=simply-discover/releases versioning=semver extractVersion=^mta/(?<version>.+)$
version: 2.3.1
```

Design: `release-process/renovate-release-integration.md`.

> Repository is private until the release process is ready to publish.
