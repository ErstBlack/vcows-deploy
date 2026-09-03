# vcows-deploy -- the deliverable.
#
# Built on a connected host; runs at a site with no network beyond the SSH tunnel
# to the hypervisor.
#
# No pip and no virtualenv. Every runtime dependency exists as an RPM (R7), and
# the application is copied to /opt/vcows and reached through PYTHONPATH -- which
# also means R7's --system-site-packages trap cannot arise, because there is no
# venv to hide the libvirt binding from.
#
# Build: `just image`, which is scripts/image-build.sh -- written so this command
# stops being one people retype. The set of paths the `-dirty` suffix is computed
# over lives in `source_revision` (scripts/lib.sh) and is not restated here: the
# copy that used to sit in this comment had already gone stale, omitting the
# Containerfile and .containerignore that #63 added to the set.
#
# The `-dirty` suffix is not decoration. The image built at e5d5a2c recorded a
# clean SHA for a commit that did not contain the `container/entrypoint.py` it
# shipped, which is the one question R5 exists to answer. `.git/` is outside the
# build context, so only the caller can compute this -- and `container/manifest.py`
# records `unknown` rather than trust anything that is not 40 hex or 40 hex plus
# `-dirty`. The paths are the ones this file COPYs. A suffix that fired for
# everything would mean nothing.
#
# **Base image: the standard one, for now.** All three were built and put through
# the same gate. Delivered (podman save | gzip): rockylinux:10 **152 MB**,
# 10-minimal **134 MB**, a builder-stage 10-ubi-micro **118 MB**. The payload
# dominates all three, so the base is worth 18 MB, not a redesign. minimal
# keeps `sh`, `rpm`, `python3` and `microdnf` while dropping `vi`, `less`, `tar`,
# `ping` and `dnf`, and pulls in a font stack the base does not have; micro drops
# `rpm` too and needs two stages. Both pass the gate, so switching later is an
# `ARG` and a package-manager name -- revisit when the size matters more than the
# tooling does.

# **Not UBI, and the reason is not compatibility.** Measured against
# registry.access.redhat.com/ubi10/ubi: `python3-libvirt`, `python3-pycdlib` and
# `libvirt-client` are all absent from BaseOS, AppStream and CodeReady
# (`dnf list` -> "No matching Packages to list"); `python3-pyyaml`,
# `python3-jsonschema`, `openssh-clients` and `glibc-minimal-langpack` are
# present. The binding this whole tool is built on is the one UBI does not ship,
# so the question needs no RHEL-vintage or RPC-compatibility argument. Settled;
# do not re-derive it.
#
# **`:10` floats across minors, so the digest needs a periodic look.** The tag
# is not a stable pointer: it resolved to 10.0 (published 2025-06-06), then 10.1
# (2025-11-16), and now 10.2 (2026-05-26) -- roughly a five-to-six month cadence,
# with no 10.3 published as of 2026-08-30. Pinning by digest freezes whichever
# minor `:10` named when somebody last looked, and Rocky maintains only the
# current minor. Checked 2026-08-30: `:10` resolves to exactly the digest below,
# so this pin is current, on the current minor, and nothing needs doing.
#
# The monthly scheduled.yml rebuild cannot notice when that stops being true --
# it builds from this ARG, so it re-pulls the same layer and re-scans it against
# the same baseline, which is what makes it look healthy. **Recheck by 2026-12**,
# when 10.3 is due, and on any month the rebuild is being looked at anyway:
#
#     skopeo inspect --no-tags docker://quay.io/rockylinux/rockylinux:10
#
# and compare `.Digest` against BASE_DIGEST. A mismatch means `:10` has moved to
# a new minor and this pin is now on an unmaintained one; re-pin, then rebuild,
# rescan and re-triage docs/cve-baseline.json by hand.
#
# Renovate was considered for exactly this pair and rejected. It is the one pin
# here it could actually track -- a customManagers regex with
# datasourceTemplate: docker -- but adopting it means a second update bot across
# both CI platforms, self-hosted on GitLab, for a value that moves twice a year,
# in a repo whose dependabot.yml already documents why it stays away from this
# file. The recheck date above is smaller and matches how docs/cve-baseline.json
# already carries `recheck` per group.
ARG BASE_IMAGE=quay.io/rockylinux/rockylinux:10
ARG BASE_DIGEST=sha256:827d37bc128288ccf160ee318bb3cb92d591164cb217e92f8bc61e3982ae1834

# `proxmoxer` is the one runtime dependency with no RPM anywhere -- measured
# 2026-09-01 across Rocky 10 baseos/appstream/crb/extras and EPEL 10, and
# repology shows no Fedora or RHEL-derived packaging at all (pyproject.toml says
# so at length). It is pure Python with `requires_dist: null`, so this stage
# installs one hash-pinned wheel and the runtime stage copies the result.
#
# **A separate stage so the delivered image never carries pip.** `dnf remove`
# afterwards would leave the layer behind and the removal is not free; a stage
# boundary is. `python3-pip` itself comes from the same repo as everything else,
# so this adds no air-gap mechanism to the *build* either.
# Declared before the first FROM so **both** stages can see them: the pydeps
# stage downloads the wheel, and the runtime stage passes the version and digest
# to the manifest. An ARG declared inside a stage is scoped to that stage, and a
# bare re-declaration in another one inherits the *global* value -- so with these
# below the FROM the manifest silently recorded an empty `pip_packages`.
ARG PROXMOXER_VERSION=2.3.0
# The wheel's own sha256 from PyPI. A second pin nothing can automate.
ARG PROXMOXER_SHA256=1c03445e95cf9c53b6e50614dbaf561e0e1eb3ec878cf45ddde4bc4421c56743
ARG PROXMOXER_URL=https://files.pythonhosted.org/packages/f3/fa/598ceae13e96ac97cf8e9b481433587b87edddb4bb9200632bd8bd80e448/proxmoxer-2.3.0-py3-none-any.whl

FROM ${BASE_IMAGE}@${BASE_DIGEST} AS pydeps

ARG PROXMOXER_VERSION
ARG PROXMOXER_SHA256
ARG PROXMOXER_URL

# `--no-deps` and `--no-index`: the wheel declares no dependencies and there is
# nothing to resolve, so pip is only being used to unpack it correctly.
#
# The canonical wheel filename, not a convenient one: pip refuses to install a
# wheel whose name it cannot parse ("is not a valid wheel filename"), because the
# name is where the version and the compatibility tags live.
#
# Three suppressions, and the directive must be the line immediately above the
# instruction -- a comment between the two makes hadolint ignore the ignore.
# DL3041 wants a dnf version pin, which this repo pins by digest instead.
# DL3040 wants `dnf clean all`, pointless in a stage nothing is copied out of
# but /pydeps. DL3013 wants a pip version pin, and the wheel is pinned harder
# than that: by exact filename and by sha256 before pip is handed it.
# hadolint ignore=DL3041,DL3040,DL3013
RUN dnf -y install --nodocs --setopt=install_weak_deps=0 python3-pip \
 && WHL="/tmp/proxmoxer-${PROXMOXER_VERSION}-py3-none-any.whl" \
 && curl -fsSLo "${WHL}" "${PROXMOXER_URL}" \
 && echo "${PROXMOXER_SHA256}  ${WHL}" | sha256sum -c - \
 && pip3 install --no-cache-dir --no-deps --no-index --target /pydeps "${WHL}" \
 && rm -f "${WHL}"


FROM ${BASE_IMAGE}@${BASE_DIGEST}

# Repeated after FROM: an ARG declared before the first FROM is out of scope
# inside the stage, and both of these are recorded in the manifest and the labels.
ARG BASE_IMAGE
ARG BASE_DIGEST
ARG PROXMOXER_VERSION
ARG PROXMOXER_SHA256

ARG VCOWS_VERSION=0.1.0.0
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown

# Taken from the manifest's own licence fields rather than asserted, and
# deliberately not exhaustive -- the true conjunction across roughly 160 packages runs to
# thousands of characters and is unreadable as a label. This names what actually
# constrains a redistributor; `/opt/vcows/manifest.json` is the authoritative
# per-package record. GPL-2.0-only is in here on purpose: glibc, util-linux-core,
# libzstd and python3-pycdlib all carry it, GPLv2 section 3 has no network-server
# option, and that single fact is why the source sidecar cannot be avoided (D22).
ARG IMAGE_LICENSES="Apache-2.0 AND GPL-2.0-only AND GPL-3.0-or-later AND LGPL-2.1-or-later AND MIT AND BSD-3-Clause AND Python-2.0.1"

# --nodocs, no weak dependencies, and the minimal langpack: the GPL source
# obligation is proportional to what ships (D22), so the closure is kept small
# for a licensing reason as much as a size one.
RUN dnf -y install --nodocs --setopt=install_weak_deps=0 epel-release \
 && dnf -y install --nodocs --setopt=install_weak_deps=0 \
      glibc-minimal-langpack \
      python3-libvirt \
      python3-pyyaml \
      python3-jsonschema \
      python3-pycdlib \
      python3-requests \
      # proxmoxer streams a multipart upload only if it can import this; without
      # it the golden image is read whole into memory and >2 GiB raises OverflowError.
      python3-requests-toolbelt \
      openssh-clients \
 && dnf -y remove epel-release \
 && dnf clean all \
 && rm -rf /var/cache/dnf /var/cache/libdnf5 /etc/yum.repos.d/epel*.repo

# Redistributing proxmoxer means shipping its licence (R3).
COPY licenses /opt/vcows/licenses

COPY orchestrator /opt/vcows/orchestrator

# The one pip-installed dependency, built in the `pydeps` stage above. Its own
# directory rather than site-packages so `rpm -qa` stays the whole truth about
# what dnf put in the image, and so the manifest can name it separately.
COPY --from=pydeps /pydeps /opt/vcows/vendor

# `vcows` rather than `python3 -m orchestrator.cli`, because the entrypoint is
# what an operator types and reads in `podman ps`.
RUN printf '#!/bin/sh\nexec /usr/bin/python3 -m orchestrator.cli "$@"\n' \
      > /usr/local/bin/vcows \
 && chmod 0755 /usr/local/bin/vcows

# The entrypoint writes ~/.ssh/config from the config's ssh_keyfile and
# known_hosts, because libvirt honours neither as a URI parameter and runs ssh.
# Container glue: cli.py stays out of anyone's home directory. See
# container/entrypoint.py for the measurements behind it.
COPY --chmod=0755 container/entrypoint.py /usr/local/bin/vcows-entrypoint

ENV PYTHONPATH=/opt/vcows:/opt/vcows/vendor \
    PYTHONDONTWRITEBYTECODE=1 \
    VCOWS_MANIFEST=/opt/vcows/manifest.json

# Last, so it describes the finished image. Everything above is already in place
# by the time `rpm -qa` runs.
COPY container/manifest.py /tmp/manifest.py
RUN VCOWS_VERSION="${VCOWS_VERSION}" GIT_SHA="${GIT_SHA}" BUILD_DATE="${BUILD_DATE}" \
    BASE_IMAGE="${BASE_IMAGE}" BASE_DIGEST="${BASE_DIGEST}" \
    PROXMOXER_VERSION="${PROXMOXER_VERSION}" \
    PROXMOXER_SHA256="${PROXMOXER_SHA256}" \
    python3 /tmp/manifest.py > /opt/vcows/manifest.json \
 && rm -f /tmp/manifest.py

# The base image labels every one of these, so an image that overrides none of
# them self-identifies to a third party as an RESF product (F16).
LABEL org.opencontainers.image.title="vcows-deploy" \
      org.opencontainers.image.description="Deploy pre-built golden qcow2 images as VMs to KVM/libvirt over qemu+ssh" \
      org.opencontainers.image.version="${VCOWS_VERSION}" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="${IMAGE_LICENSES}" \
      org.opencontainers.image.vendor="vcows" \
      org.opencontainers.image.authors="vcows" \
      org.opencontainers.image.source="https://github.com/ErstBlack/vcows-deploy" \
      org.opencontainers.image.base.name="${BASE_IMAGE}" \
      org.opencontainers.image.base.digest="${BASE_DIGEST}" \
      name="vcows-deploy" \
      version="${VCOWS_VERSION}" \
      summary="Deploy golden qcow2 images as VMs to KVM/libvirt" \
      vendor="vcows" \
      license="${IMAGE_LICENSES}"

# Explicit, because the default run directory is `runs/<deployment>/<timestamp>`
# relative to it -- so this is what makes the README's `-v ./runs:/runs` land where
# vcows writes. The Rocky base sets no WORKDIR and podman would default to `/`
# anyway; the point is that it stays true if either of those changes.
WORKDIR /

ENTRYPOINT ["/usr/local/bin/vcows-entrypoint"]
CMD ["--help"]
