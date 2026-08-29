# vcows-deploy -- the deliverable.
#
# Built on a connected host; runs at a site with no network beyond the SSH tunnel
# to the hypervisor. Everything OpenTofu needs is baked in: the provider comes
# from /opt/tofu-mirror and there is no `direct` block in the CLI config, so a
# missing provider fails immediately instead of resolving DNS and hanging.
#
# No pip and no virtualenv. Every runtime dependency exists as an RPM (R7), and
# the application is copied to /opt/vcows and reached through PYTHONPATH -- which
# also means R7's --system-site-packages trap cannot arise, because there is no
# venv to hide the libvirt binding from.
#
# Build:
#   podman build -t vcows-deploy:0.1.0.0 \
#     --build-arg GIT_SHA="$(git rev-parse HEAD)" \
#     --build-arg BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" .
#
# The provider mirror must exist at .tools/tofu-mirror first; see the Stage 2
# prerequisites. It is the one thing under .tools/ the build context admits.

ARG BASE_IMAGE=quay.io/rockylinux/rockylinux:10
ARG BASE_DIGEST=sha256:827d37bc128288ccf160ee318bb3cb92d591164cb217e92f8bc61e3982ae1834

FROM ${BASE_IMAGE}@${BASE_DIGEST}

# Repeated after FROM: an ARG declared before the first FROM is out of scope
# inside the stage, and both of these are recorded in the manifest and the labels.
ARG BASE_IMAGE
ARG BASE_DIGEST

ARG VCOWS_VERSION=0.1.0.0
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown

# Pinned exactly, and the checksum is from the release's published SHA256SUMS.
# The GitHub release RPM rather than a tarball (D7), so it lands in the RPM
# database and the manifest reads its version and licence like everything else.
ARG TOFU_VERSION=1.12.6
ARG TOFU_RPM_SHA256=547fe4544d3091ede04478f143fbb17bb0e010999237d904bf8950ad7542848f

ARG PROVIDER_VERSION=0.9.8
ARG PROVIDER_SHA256=061e5187853729e1d8ba20938402ad6e778b4097436925d0bef7741c8aa26ee1
ARG PROVIDER_LOCK_HASH=h1:yqZeKoJ+EZc3687/+ZBqBmtwzvBPLNwaEHW74+bSc6Y=

# Taken from the manifest's own licence fields rather than asserted, and
# deliberately not exhaustive -- the true conjunction across 161 packages runs to
# thousands of characters and is unreadable as a label. This names what actually
# constrains a redistributor; `/opt/vcows/manifest.json` is the authoritative
# per-package record. GPL-2.0-only is in here on purpose: glibc, util-linux-core,
# libzstd and python3-pycdlib all carry it, GPLv2 section 3 has no network-server
# option, and that single fact is why the source sidecar cannot be avoided (D22).
ARG IMAGE_LICENSES="MPL-2.0 AND Apache-2.0 AND GPL-2.0-only AND GPL-3.0-or-later AND LGPL-2.1-or-later AND MIT AND BSD-3-Clause AND Python-2.0.1"

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
      openssh-clients \
 && dnf clean all \
 && rm -rf /var/cache/dnf /var/cache/libdnf5

RUN curl -fsSLo /tmp/tofu.rpm \
      "https://github.com/opentofu/opentofu/releases/download/v${TOFU_VERSION}/tofu_${TOFU_VERSION}_amd64.rpm" \
 && echo "${TOFU_RPM_SHA256}  /tmp/tofu.rpm" | sha256sum -c - \
 && rpm -i /tmp/tofu.rpm \
 && rm -f /tmp/tofu.rpm

# The provider, and the CLI config that is the only thing pointing at it.
COPY .tools/tofu-mirror /opt/tofu-mirror
COPY container/tofurc /opt/tofu/tofurc

# Redistributing the provider means shipping its licence, which upstream does not
# carry in 0.9.x. The provenance note explains why that is a gap rather than a
# revocation (R3).
COPY licenses /opt/vcows/licenses

COPY orchestrator /opt/vcows/orchestrator

# The committed lock, in the module directory the CLI stages from, so a deploy at
# a site cannot silently select a differently-built provider. A lock produced
# against a registry records different hashes than one produced against a mirror,
# and the mismatch reads like corruption (R6).
COPY docs/provider-0.9.8.lock.hcl \
     /opt/vcows/orchestrator/backends/libvirt/tofu/.terraform.lock.hcl

# `vcows` rather than `python3 -m orchestrator.cli`, because the entrypoint is
# what an operator types and reads in `podman ps`.
RUN printf '#!/bin/sh\nexec /usr/bin/python3 -m orchestrator.cli "$@"\n' \
      > /usr/local/bin/vcows \
 && chmod 0755 /usr/local/bin/vcows

ENV PYTHONPATH=/opt/vcows \
    PYTHONDONTWRITEBYTECODE=1 \
    VCOWS_MANIFEST=/opt/vcows/manifest.json \
    CHECKPOINT_DISABLE=1 \
    TF_CLI_CONFIG_FILE=/opt/tofu/tofurc

# Last, so it describes the finished image. Everything above is already in place
# by the time `rpm -qa` runs.
COPY container/manifest.py /tmp/manifest.py
RUN VCOWS_VERSION="${VCOWS_VERSION}" GIT_SHA="${GIT_SHA}" BUILD_DATE="${BUILD_DATE}" \
    BASE_IMAGE="${BASE_IMAGE}" BASE_DIGEST="${BASE_DIGEST}" \
    PROVIDER_VERSION="${PROVIDER_VERSION}" PROVIDER_SHA256="${PROVIDER_SHA256}" \
    PROVIDER_LOCK_HASH="${PROVIDER_LOCK_HASH}" \
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

ENTRYPOINT ["/usr/local/bin/vcows"]
CMD ["--help"]
