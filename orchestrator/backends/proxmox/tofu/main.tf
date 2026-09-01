terraform {
  required_version = ">= 1.12.0"

  required_providers {
    proxmox = {
      source = "bpg/proxmox"
      // Pinned exactly, not constrained, for the reason the libvirt module gives:
      // `tofu providers mirror` resolves the newest version satisfying a
      // constraint at build time while the lock pins exactly, so a range makes
      // the module, the mirror and the lock three independently drifting facts.
      //
      // It matters more here. bpg/proxmox is pre-1.0 and its own documentation
      // states it does not guarantee backward compatibility across minor
      // versions, so a bump is a deliberate edit with the release notes read.
      version = "= 0.111.1"
    }
  }
}

provider "proxmox" {
  endpoint = var.endpoint
  insecure = var.insecure

  // No api_token here. The provider reads PROXMOX_VE_API_TOKEN from its own
  // environment, which orchestrator/tofu.py passes through untouched -- so the
  // credential never lands in the tfvars file in the run directory.
  //
  // No ssh block either, and that is the design rather than an omission. The
  // provider needs SSH only for `snippets` and `backup` uploads; `import`, `iso`
  // and `vztmpl` all go through the HTTP API. Shipping cloud-init as a NoCloud
  // seed ISO instead of a snippet is what keeps this backend on one credential.
}

// The shared golden image. Uploaded once per cluster and never destroyed:
// teardown runs by marker and files carry no markers. That is intended -- every
// VM imports from it, and re-pushing a multi-GB image is the cost this avoids.
resource "proxmox_virtual_environment_file" "image" {
  count = var.image.create ? 1 : 0

  content_type = "import"
  datastore_id = var.import_datastore
  node_name    = var.node

  source_file {
    path      = var.image.source
    file_name = var.image.file_name
    checksum  = var.image.checksum != "" ? var.image.checksum : null
  }
}

locals {
  image_id = var.image.create ? proxmox_virtual_environment_file.image[0].id : var.image.volid
}

// The NoCloud seed, one per VM, uploaded as an ISO rather than written as a
// snippet. Snippets cannot be uploaded through the API at all -- the endpoint
// takes only iso, vztmpl and import -- so the provider falls back to SSH/SFTP
// for them, which would mean this backend needed an SSH credential beside its
// API token. Proxmox does the same packaging internally when `cicustom` is set.
resource "proxmox_virtual_environment_file" "seed" {
  for_each = var.vms

  content_type = "iso"
  datastore_id = var.import_datastore
  node_name    = var.node

  source_file {
    path = each.value.seed_iso
    // Never `vm-<vmid>-cloudinit.iso`: Proxmox pattern-matches that name,
    // assumes it generated the file, and fails the VM's start task trying to
    // regenerate it. cloudinit.seed_name derives `<name>-seed.iso`.
    file_name = each.value.seed_name
  }

  // Nothing here reads an image attribute, so without this edge the seeds are an
  // independent branch of the graph. That matters because a failed image upload
  // makes OpenTofu skip its descendants while independent branches keep running
  // and are written to state -- the seeds would survive as files destroy can
  // never reach, because only VMs carry markers. This line makes a partial apply
  // a no-op apply. The libvirt module carries the same edge for the same reason.
  depends_on = [proxmox_virtual_environment_file.image]
}

resource "proxmox_virtual_environment_vm" "vm" {
  for_each = var.vms

  name      = each.value.vm_name
  node_name = var.node

  // The durable record of what vcows created. The state file is a convenience;
  // this is the truth, and it is what destroy discovers by. One prefixed line,
  // so an operator can write notes above it without breaking ownership.
  description = each.value.description

  bios    = each.value.bios
  machine = each.value.machine

  // Without this a node reboot leaves every VM vcows created powered off, and
  // the next run does not say so: discovery reports them as ours and skips them,
  // so the deploy prints `nothing to create` and exits 0.
  on_boot = true
  started = true

  // Required for OpenTofu to be able to destroy a running VM. Not what tears
  // vcows' own VMs down -- that is `vcows destroy`, through the API by marker --
  // but a plan that cannot destroy what it created is a trap for anyone who runs
  // `tofu destroy` in a run directory by hand.
  stop_on_destroy = true

  cpu {
    cores = each.value.vcpus
    // All hypervisors are confirmed Haswell or newer, so there is no migration
    // constraint to trade guest performance for. Same call as the libvirt
    // module's host-passthrough.
    type = "host"
  }

  memory {
    dedicated = each.value.memory_mib
  }

  operating_system {
    type = each.value.os_type
  }

  disk {
    datastore_id = var.datastore
    // The whole point of the `import` content type: PVE converts the uploaded
    // qcow2 into this datastore's native format as it imports.
    import_from = local.image_id
    interface   = "scsi0"
    size        = each.value.disk_gb
    // Without discard the guest's deletes and `fstrim` never reach the
    // underlying store, so a disk only ever ratchets toward its declared size on
    // a datastore that belongs to somebody else and that nothing prunes.
    discard = "on"
    ssd     = true
  }

  // Only for OVMF. A seabios VM given an EFI disk is a configuration PVE
  // accepts and no guest uses.
  dynamic "efi_disk" {
    for_each = each.value.bios == "ovmf" ? [1] : []
    content {
      datastore_id = var.datastore
      // PVE allocates and formats the vars disk itself, which is the whole
      // reason this backend needs none of libvirt's loader/nvram_template
      // host paths.
      type = "4m"
    }
  }

  cdrom {
    file_id = proxmox_virtual_environment_file.seed[each.key].id
  }

  dynamic "network_device" {
    for_each = each.value.nics
    content {
      bridge = network_device.value.bridge
      model  = network_device.value.model
      // Derived at render time because cloud-init's network-config matches an
      // interface by MAC to apply the static address -- interface names are
      // assigned by the guest kernel and are not knowable from here.
      mac_address = network_device.value.mac
      vlan_id     = network_device.value.vlan_id
    }
  }

  // The disk first, then the seed. cloud-init reads the seed off the CD-ROM
  // wherever it sits in the order, but a guest that fails to boot from disk
  // should not silently try to boot the cidata ISO.
  boot_order = ["scsi0", "ide2"]

  // No `initialization` block, deliberately. It is PVE's own cloud-init drive,
  // and two cidata sources means cloud-init picks one non-deterministically.
  // vcows ships its own seed ISO; that is the datasource.
}
