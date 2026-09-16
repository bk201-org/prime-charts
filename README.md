# Harvester Prime Charts

Helm charts maintained for Harvester Prime are stored under `charts/` and are published as private OCI artifacts after they pass linting and installation in a kind cluster.

## Branches and versions

- `main` contains development charts. It runs CI but does not publish.
- `vX.Y` branches contain charts compatible with Harvester `vX.Y.x` and publish changed charts after a successful push. For example, use `v1.9` for the Harvester 1.9 release line.
- Each chart has its own SemVer version. Versions must be unique across all stable branches because every release line shares the same OCI package.
- Increment `version` in the affected `Chart.yaml` for every stable chart change. Use patch versions for fixes, minor versions for backward-compatible features, and major versions for breaking changes.
- `appVersion` tracks the packaged application independently of the chart version.

Create a new stable branch from the tested revision on `main`. Continue new development on `main`, and backport applicable fixes to each maintained stable branch.

## Continuous integration

Pull requests and pushes to `main` and `vX.Y` branches run the following checks for changed charts:

1. `ct lint`
2. `helm package`
3. Create a Kubernetes 1.35 kind cluster
4. `ct install` and wait for chart Deployments to become Ready

Changes to shared CI configuration and manually dispatched runs test every chart. A manual overwrite tests only its requested chart. Stable-branch publication depends on the successful completion of these checks.

Chart-specific kind overrides live in each chart's `ci/kind-values.yaml`. Registry values in these files are placeholders (e.g. `${FORKLIFT_REGISTRY}`) substituted from repository secrets before the kind cluster installs the charts.

The chart pipeline implementation and its tests live together under `scripts/ci/chart_pipeline/`. Run the tests locally with `python3 -m unittest discover --start-directory scripts/ci/chart_pipeline/tests --top-level-directory . --verbose`. They require Python 3.13, Helm, and chart-testing, use temporary Git repositories and a filesystem-backed fake OCI registry, and do not push packages or require registry credentials. Chart discovery is delegated to `ct list-changed`; the repository helper adds event-specific base revisions, shared-file handling, and manual overwrite selection. The CI scripts write progress logs to stderr so stdout remains safe for machine-readable values. Set `VERBOSE=true` to include comparison revisions, temporary paths, selected chart names, and verification attempts.

## OCI registry

Charts are published as private OCI artifacts to the Rancher Prime registry, one chart per job (the publish-and-sign job runs as a matrix, one instance per changed chart):

```text
oci://<prime-registry>/<prime-registry-username>/<chart>
```

The registry host and namespace come from the `PRIME_REGISTRY` and `PRIME_REGISTRY_USERNAME` repository secrets rather than from `github.repository`, so they can be replaced later without changing chart layout or versions.

Every chart actually pushed (i.e. not skipped because it's already published with identical content) is signed keylessly with [cosign](https://github.com/sigstore/cosign) using the workflow's GitHub OIDC identity, and a SLSA provenance attestation is generated and pushed to the registry alongside it via `actions/attest-build-provenance`. Verify a published chart with:

```shell
cosign verify \
  --certificate-identity-regexp 'https://github.com/harvester/prime-charts/.github/workflows/ci.yaml@.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  <prime-registry>/<prime-registry-username>/<chart>@<digest>

gh attestation verify oci://<prime-registry>/<prime-registry-username>/<chart>@<digest> \
  -R harvester/prime-charts
```

Because the registry is private, local users need Prime registry credentials to pull charts:

```shell
helm registry login <prime-registry> --username <prime-registry-username>
helm show chart oci://<prime-registry>/<prime-registry-username>/forklift-operator --version 0.1.0
helm install forklift oci://<prime-registry>/<prime-registry-username>/forklift-operator \
  --version 0.1.0 \
  --namespace forklift \
  --create-namespace
```

The CI job only reads repository contents. The publish-and-sign job additionally holds `id-token: write` (cosign and attestation OIDC signing) and `attestations: write` (GitHub-hosted attestation records).

Published name/version pairs are immutable during normal push-based publication. To intentionally replace an existing artifact, manually run **Publish Prime Charts** from the target `vX.Y` branch and enter the exact `chart-name@version` in the `overwrite_existing` field, for example `harvester-mcp-server@1.9.1-dev.1`. The workflow tests only that chart, confirms the requested identity against `Chart.yaml`, overwrites the OCI tag without deleting it, pulls the result back to verify its contents, and signs/attests the overwritten artifact like any other push. This escape hatch accepts release and prerelease versions and should be used only when changing an existing artifact is intentional.
