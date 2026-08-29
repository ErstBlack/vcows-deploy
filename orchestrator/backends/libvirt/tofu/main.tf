terraform {
  required_version = ">= 1.12.0"

  required_providers {
    libvirt = {
      source = "dmacvicar/libvirt"
      // Pinned exactly, not constrained. `tofu providers mirror` resolves the
      // newest version satisfying a constraint at build time while the lock
      // pins exactly, so a range makes the module, the mirror and the lock three
      // independently drifting facts (findings.md R6).
      version = "= 0.9.8"
    }
  }
}

provider "libvirt" {
  uri = var.uri
}

// The shared golden image. Created once per host and never destroyed: teardown
// runs through Python by marker, and volumes cannot carry markers. That is
// intended -- every deployment's overlays back onto it, and re-pushing multi-GB
// images through the SSH tunnel is the cost this avoids.
resource "libvirt_volume" "base" {
  count = var.base_volume.create ? 1 : 0

  name   = var.base_volume.name
  pool   = var.pool
  target = { format = { type = "qcow2" } }
  create = { content = { url = var.base_volume.source } }
}

locals {
  base_path = var.base_volume.create ? libvirt_volume.base[0].path : var.base_volume.path
}

// Per-VM overlay. `capacity` is set HERE and only here: spike A4 confirmed that
// creating the base at a declared size and then uploading into it silently
// discards that size, with a zero exit and no warning.
resource "libvirt_volume" "overlay" {
  for_each = var.vms

  name          = each.value.overlay_name
  pool          = var.pool
  capacity      = each.value.disk_bytes
  capacity_unit = "bytes"
  target        = { format = { type = "qcow2" } }

  backing_store = {
    path   = local.base_path
    format = { type = "qcow2" }
  }
}

// The NoCloud seed. A pool volume rather than libvirt_cloudinit_disk, which
// stages its ISO in os.TempDir() and treats the file's absence as a deleted
// resource -- so a container's empty /tmp makes it, its volume and the domain
// all show as needing recreation on every run (findings.md F2).
resource "libvirt_volume" "seed" {
  for_each = var.vms

  name   = each.value.seed_name
  pool   = var.pool
  target = { format = { type = "raw" } }
  create = { content = { url = each.value.seed_iso } }
}

resource "libvirt_domain" "vm" {
  for_each = var.vms

  name        = each.value.domain_name
  type        = "kvm"
  vcpu        = each.value.vcpus
  memory      = each.value.memory_mib
  memory_unit = "MiB"
  running     = true

  // The durable record of what vcows created. The state file is a convenience;
  // this is the truth, and it is what destroy discovers by.
  metadata = { xml = each.value.marker_xml }

  // All hypervisors are confirmed Haswell or newer, so there is no migration
  // constraint to trade guest performance for.
  cpu = { mode = "host-passthrough" }

  os = {
    type         = "hvm"
    type_arch    = "x86_64"
    type_machine = each.value.machine

    // With loader and nvram_template unset, this is the whole firmware config
    // and libvirt selects from the host's descriptors.
    firmware = each.value.firmware == "efi" ? "efi" : null

    loader        = each.value.loader
    loader_format = each.value.loader_format
    loader_type   = each.value.loader != null ? "pflash" : null

    // A string, not a boolean. The provider's generated docs disagree with its
    // own schema here; `tofu providers schema -json` is the ground truth and
    // spike A6 pinned it for exactly this reason.
    loader_readonly = each.value.loader != null ? "yes" : null

    nv_ram = each.value.nvram_template != null ? {
      nv_ram = "/var/lib/libvirt/qemu/nvram/${each.value.domain_name}_VARS.fd"
      // The variables file follows the firmware build it was templated from, so
      // one config field settles both.
      format          = each.value.loader_format
      template        = each.value.nvram_template
      template_format = each.value.loader_format
    } : null

    boot_devices = [{ dev = "hd" }]
  }

  devices = {
    disks = [
      {
        device = "disk"
        driver = { name = "qemu", type = "qcow2" }
        // The volume's computed path, never source.volume{pool,volume}. A
        // type='volume' disk writes <source pool= volume=> into the persistent
        // XML, which is not what the destroy path parses -- this keeps one
        // uniform parse there.
        source = { file = { file = libvirt_volume.overlay[each.key].path } }
        target = { dev = "vda", bus = "virtio" }
      },
      {
        device    = "cdrom"
        read_only = true
        driver    = { name = "qemu", type = "raw" }
        source    = { file = { file = libvirt_volume.seed[each.key].path } }
        target    = { dev = "sda", bus = "sata" }
      },
    ]

    interfaces = [
      for n in each.value.nics : {
        mac   = { address = n.mac }
        model = { type = n.model }
        source = {
          network = n.network != null ? { network = n.network } : null
          bridge  = n.bridge != null ? { bridge = n.bridge } : null
        }
      }
    ]

    // Nothing adds a console automatically -- the provider writes exactly the
    // XML it is given. Without these, a VM that fails cloud-init at an air-gapped
    // site is unreachable and un-inspectable, and the recovery is hand-editing
    // domain XML on the hypervisor. Omitting `source` leaves the char device
    // type unset, which libvirt fills in as `pty`.
    serials  = [{ target = { port = 0 } }]
    consoles = [{ target = { type = "serial", port = 0 } }]
  }
}
