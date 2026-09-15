# OCI Chart Publishing with kind-Based CI

## Summary

- Keep `main` validation-only and publish from maintained `vX.Y` branches such as `v1.9`.
- Test changed charts by linting, packaging, creating a kind cluster, installing the charts, and waiting for their Deployments to become Ready.
- Publish only after the kind test succeeds.
- Publish each chart as a private OCI package under `oci://ghcr.io/<owner>/<repo>/<chart>`.

## CI and publication flow

- Run CI for pull requests and pushes involving `main` or `vX.Y` branches.
- Keep change detection and publication logic with their Python tests under `scripts/ci/chart_pipeline/`; keep the workflow focused on orchestration and permissions.
- Exercise change detection and publication behavior with Python tests backed by temporary Git repositories and a fake OCI registry.
- Detect charts changed between the event base and head with `ct list-changed`. Test all charts when shared CI configuration changes or on a manual run.
- Run `ct lint`, with version increment checks disabled on `main` and required on stable branches.
- Use chart-local `ci/kind-values.yaml` files for runnable test images.
- Create a Kubernetes 1.35 kind cluster and run `ct install`, which installs each selected chart in an isolated namespace and waits for its Deployment.
- Give CI and pull-request jobs only `contents: read`; they do not pull charts from GHCR.
- Give only the stable-branch publication job `contents: read` and `packages: write`.
- Before publishing, check GHCR for the name/version. Skip identical content on retry and reject different content at an existing version during normal pushes.
- Permit an explicit manual overwrite for exactly one `chart-name@version` after that chart passes the full test flow. Do not require deletion permission and do not restrict the escape hatch to prerelease versions.
- Pull every published chart back and verify its complete contents.

## Branch and version policy

- `main` is the development branch and never publishes charts.
- A branch named `vX.Y` contains charts compatible with Harvester `vX.Y.x` and publishes after every successful chart-changing push.
- Charts use independent SemVer versions that are globally unique across stable branches.
- Increment patch versions for fixes, minor versions for backward-compatible features, and major versions for breaking chart changes.
- Keep `appVersion` independent and aligned with the deployed workload.

## Private package access

- The publishing workflow authenticates with its repository-scoped `GITHUB_TOKEN`.
- Local consumers must authenticate to `ghcr.io` with a token that has `read:packages` before pulling or installing a private chart.
- Workflows in other repositories must be granted read access to each chart package before their `GITHUB_TOKEN` can pull it.
- Pull-request CI does not need `packages: read` unless private OCI dependencies or private test images are introduced later.

## Temporary image configuration

- Use the public upstream Forklift operator image for its kind readiness test.
- Keep an explicit `REPLACE_ME_*` MCP image reference in its kind values file for the maintainer to replace.
- Fail before cluster creation while a placeholder remains, rather than timing out in `ImagePullBackOff`.
- Assume the final Prime MCP image will be anonymously pullable; registry authentication is deferred.

## Acceptance criteria

- `main` changes pass lint, package, kind install, and readiness checks without publishing.
- Stable chart changes cannot publish unless the same revision passes the full kind test.
- A stable chart change without a version increment fails.
- Invalid Kubernetes objects, failed image pulls, failed readiness probes, and unavailable Deployments fail CI.
- Normal push-based publication never overwrites an existing OCI name/version.
- A manual overwrite affects only the exact chart and version entered by the operator.
