# Simply Discover Releases

A catalogue of Simply Discover releases. For each release it holds a machine-readable manifest that
names the exact container images that make up the release, by version and digest.

This repository contains no source code, no images and no credentials. Publishing a release here does
not grant access to its images: image access is controlled separately, through your Simply Discover
licence.

## Products

Each product is released and versioned independently, in its own directory.

| Product | What it contains |
|---|---|
| `dotnet-app` | The application: server, worker and react images, released together as one version |
| `mta` | The SimplyMTA journaling ingress image |
| `solr` | The Solr image with the Simply Discover search configuration |

## Layout

```text
├── schema/release-manifest.v1.schema.json   the manifest schema
├── tools/publish_release.py                 how releases are written and validated
└── <product>/versions/
    ├── <version>.json                       release manifest
    └── <version>.md                         release notes
```

## Releases and tags

- A release is one SemVer version of one product, for example `2.5.2` of `mta`.
- The release is marked by the Git tag `<product>/<version>`, for example `mta/2.5.2`. Tags are
  created last, only after every image in the release has been published and its digest verified, so
  a tag always points at a complete release.
- Tags and manifests are immutable. A faulty release is withdrawn in a later commit
  (`supportStatus` / `supersededBy`) or replaced by a new version. Published tags are never moved.
- A release can be partial: for example a fix to one component of `dotnet-app` is a new version in
  which only that image changed. Unchanged images keep their earlier tag and digest, and the manifest's
  `baseRelease` says which release they came from.

## Reading a release

Always read a manifest from its tag, never from `main`:

```sh
git clone --depth 1 --branch mta/2.5.2 https://github.com/simply-discover/releases
cat releases/mta/versions/2.5.2.json
```

A manifest lists every image of the release with its `registry`, `repository`, `tag` and `digest`.
Deploy by digest, and treat a digest that differs from the manifest as a failed verification.

## Finding new releases with Renovate

The tags are what Renovate looks for. Keep the version you intend to run of each product in a small
file in your own repository, one annotated line per product you use:

```yaml
# simplydiscover.release.yaml
# renovate: datasource=github-tags depName=dotnet-app packageName=simply-discover/releases versioning=semver extractVersion=^dotnet-app/(?<version>.+)$
dotnet-app: 8.3.1
# renovate: datasource=github-tags depName=mta packageName=simply-discover/releases versioning=semver extractVersion=^mta/(?<version>.+)$
mta: 2.5.1
```

and add a custom manager to your existing `renovate.json`:

```json
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "customManagers": [
    {
      "customType": "regex",
      "managerFilePatterns": ["/^simplydiscover\\.release\\.yaml$/"],
      "matchStrings": [
        "# renovate: datasource=(?<datasource>\\S+) depName=(?<depName>\\S+) packageName=(?<packageName>\\S+) versioning=(?<versioning>\\S+) extractVersion=(?<extractVersion>\\S+)\\s+\\S+:\\s*(?<currentValue>\\S+)"
      ]
    }
  ]
}
```

- `depName` is the product (`dotnet-app`, `mta` or `solr`); each product gets its own pull request.
  `extractVersion` selects that product's tags and strips the `<product>/` prefix.
- This adds a manager alongside whatever you already use. Do not set `enabledManagers` unless you
  mean to switch off Renovate's other managers.
- Renovate opens a pull request that changes only the version. Merging that pull request is your
  approval of the new release; Renovate itself deploys nothing and needs no credentials for this
  repository.
