# Harvester Prime Charts

Helm charts maintained for Harvester Prime are stored under `charts/` and are published as private OCI artifacts after they pass linting and installation in a kind cluster.

## Branches and versions

- `main` contains development charts. It runs CI but does not publish.
- `vX.Y` branches contain charts compatible with Harvester `vX.Y.x` and publish changed charts after a successful push. For example, use `v1.9` for the Harvester 1.9 release line.
- Each chart has its own SemVer version. Versions must be unique across all stable branches because every release line shares the same OCI package.
- Increment `version` in the affected `Chart.yaml` for every stable chart change. Use patch versions for fixes, minor versions for backward-compatible features, and major versions for breaking changes.
- `appVersion` tracks the packaged application independently of the chart version.

Create a new stable branch from the tested revision on `main`. Continue new development on `main`, and backport applicable fixes to each maintained stable branch.

### Adding a new chart on `main`

1. Scaffold the chart under `charts/`:
   ```shell
   helm create charts/my-app
   ```
2. Fill in `Chart.yaml` (name, description, starting `version: 0.1.0`, `appVersion`), `values.yaml`, and `templates/` for the real deployment.
3. Add a chart-local `ci/kind-values.yaml` with a runnable test image so `ct install` can bring the chart up in the ephemeral kind cluster. If the image comes from a private registry, reference it as `${MY_REGISTRY}` and wire a matching repository secret into the "Substitute test image values" step in `.github/workflows/ci.yaml`.
4. Open a PR against `main`. CI lints, packages, and installs the chart in kind; `main` never publishes, so nothing is pushed to the Prime registry yet.
5. Merge once CI passes. The chart ships the next time a stable branch is cut from `main`.

### Bumping a chart version on a stable branch

1. Check out the target `vX.Y` branch, e.g. `v1.9`.
2. Make the chart change, then bump `version` in that chart's `Chart.yaml` — patch for fixes, minor for backward-compatible features, major for breaking changes:
   ```diff
    apiVersion: v2
    name: forklift-operator
   -version: 1.9.2
   +version: 1.9.3
    appVersion: "1.8.2"
   ```
3. Push (or merge a PR) to `v1.9`. CI lints (rejecting a missing version bump on a stable branch), packages, and installs the chart; on success, `publish-prime` publishes `forklift-operator@1.9.3` and signs/attests it.
4. Confirm the publish, e.g. `helm show chart oci://<prime-registry>/<prime-registry-username>/forklift-operator --version 1.9.3`.

### Overwriting a published chart

By default a chart's content can't be altered without bumping its `version`, so a published `chart-name@version` never gets overwritten through the normal flow.

`overwrite_existing` is a `workflow_dispatch` input on **Publish Prime Charts** that bypasses this and intentionally republishes one already-published `chart-name@version`. It only works when the workflow is run from a `vX.Y` stable branch — `main` never publishes, so `find_charts.py` rejects the request (`Existing charts can only be overwritten from a vX.Y branch.`) if the selected branch/ref isn't a stable branch.

To use it, run the workflow from the target stable branch and pass the exact chart name and version from that chart's `Chart.yaml`, for example:

```shell
gh workflow run "Publish Prime Charts" \
  --ref v1.9 \
  -f overwrite_existing=harvester-mcp-server@1.9.1-dev.1
```

(equivalently, run it from the Actions tab: select **Publish Prime Charts**, choose branch `v1.9`, and fill in `overwrite_existing`.)

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

Every chart selected for publication is pushed unconditionally (there is no skip-if-unchanged check; `helm push` overwrites an existing tag), then signed keylessly with [cosign](https://github.com/sigstore/cosign) using the workflow's GitHub OIDC identity, and a SLSA provenance attestation is built and pushed to the registry alongside it with `cosign attest --type slsaprovenance1`. Verify a published chart with:

```shell
identity_regexp='https://github.com/harvester/prime-charts/.github/workflows/ci.yaml@.*'
chart_ref=<prime-registry>/<prime-registry-username>/<chart>@<digest>

cosign verify \
  --certificate-identity-regexp "${identity_regexp}" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "${chart_ref}"

cosign verify-attestation \
  --type slsaprovenance1 \
  --certificate-identity-regexp "${identity_regexp}" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "${chart_ref}"
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

The CI job only reads repository contents. The publish-and-sign job additionally holds `id-token: write` for cosign's and the provenance attestation's OIDC signing.
