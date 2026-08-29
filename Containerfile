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
#   ship='orchestrator container licenses docs/provider-0.9.8.lock.hcl'
#   podman build -t vcows-deploy:0.1.0.0 \
#     --build-arg GIT_SHA="$(git rev-parse HEAD)$(git status --porcelain -- $ship \
#                             | grep -q . && printf -- -dirty)" \
#     --build-arg BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" .
#
# The `-dirty` suffix is not decoration. The image built at e5d5a2c recorded a
# clean SHA for a commit that did not contain the `container/entrypoint.py` it
# shipped, which is the one question R5 exists to answer. `.git/` is outside the
# build context, so only the caller can compute this -- and `container/manifest.py`
# records `unknown` rather than trust anything that is not 40 hex or 40 hex plus
# `-dirty`. The paths are the ones this file COPYs: a change under `docs/` or
# `tests/` cannot reach the image, and flagging the build for one would make the
# suffix mean nothing.
#
# The provider mirror must exist at .tools/tofu-mirror first; see the Stage 2
# prerequisites. It is the one thing under .tools/ the build context admits.
#
# **Base image: the standard one, for now.** All three were built and put through
# the same gate. Delivered (podman save | gzip): rockylinux:10 **152 MB**,
# 10-minimal **134 MB**, a builder-stage 10-ubi-micro **118 MB**. The payload
# dominates all three -- the tofu binary alone is 115 MB uncompressed and the
# provider another 26 MB -- so the base is worth 18 MB, not a redesign. minimal
# keeps `sh`, `rpm`, `python3` and `microdnf` while dropping `vi`, `less`, `tar`,
# `ping` and `dnf`, and pulls in a font stack the base does not have; micro drops
# `rpm` too and needs two stages. Both pass the gate, so switching later is an
# `ARG` and a package-manager name -- revisit when the size matters more than the
# tooling does.

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

# No lock hash here: the manifest reads the version and the `h1:` hash out of the
# committed lock below, which is the file the deploy actually installs from. Two
# records of one fact is how a manifest ends up naming a provider the image does
# not contain.
ARG PROVIDER_VERSION=0.9.8
ARG PROVIDER_SHA256=061e5187853729e1d8ba20938402ad6e778b4097436925d0bef7741c8aa26ee1

# Taken from the manifest's own licence fields rather than asserted, and
# deliberately not exhaustive -- the true conjunction across roughly 160 packages runs to
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
 && dnf -y remove epel-release \
 && dnf clean all \
 && rm -rf /var/cache/dnf /var/cache/libdnf5 /etc/yum.repos.d/epel*.repo

RUN curl -fsSLo /tmp/tofu.rpm \
      "https://github.com/opentofu/opentofu/releases/download/v${TOFU_VERSION}/tofu_${TOFU_VERSION}_amd64.rpm" \
 && echo "${TOFU_RPM_SHA256}  /tmp/tofu.rpm" | sha256sum -c - \
 && rpm -i /tmp/tofu.rpm \
 && rm -f /tmp/tofu.rpm

# The provider, and the CLI config that is the only thing pointing at it.
COPY .tools/tofu-mirror /opt/tofu-mirror
COPY container/tofurc /opt/tofu/tofurc

# The mirror is a directory in somebody's build context, assembled by a command
# nothing here can see. `tofu init` will verify the zip against the lock's `h1:`
# hash -- but only at a site, inside a deploy, which is the wrong place to learn
# that the wrong artifact was baked in. This says it at build time.
RUN echo "${PROVIDER_SHA256}  /opt/tofu-mirror/registry.opentofu.org/dmacvicar/libvirt/terraform-provider-libvirt_${PROVIDER_VERSION}_linux_amd64.zip" \
  | sha256sum -c -

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

# The entrypoint writes ~/.ssh/config from the config's ssh_keyfile and
# known_hosts, because neither libvirt nor the provider honours those as URI
# parameters and both run ssh. Container glue: cli.py stays out of anyone's home
# directory. See container/entrypoint.py for the measurements behind it.
COPY container/entrypoint.py /usr/local/bin/vcows-entrypoint
RUN chmod 0755 /usr/local/bin/vcows-entrypoint

ENV PYTHONPATH=/opt/vcows \
    PYTHONDONTWRITEBYTECODE=1 \
    VCOWS_MANIFEST=/opt/vcows/manifest.json \
    CHECKPOINT_DISABLE=1 \
    TF_CLI_CONFIG_FILE=/opt/tofu/tofurc \
    TF_PLUGIN_CACHE_DIR=/opt/tofu/plugin-cache

# Warm the plugin cache at build time. Without it `init` *copies* a 26 MB
# provider into every run directory -- and D40 makes every deploy a new one, so
# the cost recurs forever. With it, `.terraform` is symlinks into this directory
# and a run directory holds nothing but its own artifacts. Costs 26 MB once.
RUN mkdir -p "${TF_PLUGIN_CACHE_DIR}" /tmp/warm \
 && cp /opt/vcows/orchestrator/backends/libvirt/tofu/*.tf \
       /opt/vcows/orchestrator/backends/libvirt/tofu/.terraform.lock.hcl /tmp/warm/ \
 && (cd /tmp/warm && tofu init -input=false -no-color > /dev/null) \
 && rm -rf /tmp/warm

# Last, so it describes the finished image. Everything above is already in place
# by the time `rpm -qa` runs.
COPY container/manifest.py /tmp/manifest.py
RUN VCOWS_VERSION="${VCOWS_VERSION}" GIT_SHA="${GIT_SHA}" BUILD_DATE="${BUILD_DATE}" \
    BASE_IMAGE="${BASE_IMAGE}" BASE_DIGEST="${BASE_DIGEST}" \
    PROVIDER_SHA256="${PROVIDER_SHA256}" \
    PROVIDER_LOCK=/opt/vcows/orchestrator/backends/libvirt/tofu/.terraform.lock.hcl \
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
