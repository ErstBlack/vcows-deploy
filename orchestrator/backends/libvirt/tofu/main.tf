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

  name = each.value.seed_name
  pool = var.pool

  // `iso`, not `raw`. libvirt inspects the uploaded content and reports the
  // format it detects, so declaring `raw` made the provider's post-apply read
  // disagree with its own plan -- "Provider produced inconsistent result after
  // apply: .target.format.type: was raw, but now iso" -- and failed the apply
  // after the volume had already been written. Found in the acceptance run.
  target = { format = { type = "iso" } }
  create = { content = { url = each.value.seed_iso } }

  // Nothing here reads a base attribute, so without this edge these seeds are an
  // independent branch of the graph. That matters because the provider's Create
  // calls StorageVolCreateXML with no lookup and no adoption path: naming an
  // existing volume fails hard. OpenTofu then skips the failed vertex's
  // descendants -- every overlay and domain -- while independent branches keep
  // running and are written to state. The seeds would survive as volumes destroy
  // can never reach, because volumes carry no marker. This one line makes a
  // partial apply a no-op apply.
  depends_on = [libvirt_volume.base]
}

resource "libvirt_domain" "vm" {
  for_each = var.vms

  name        = each.value.domain_name
  type        = "kvm"
  vcpu        = each.value.vcpus
  memory      = each.value.memory_mib
  memory_unit = "MiB"
  running     = true

  // Without this a hypervisor reboot leaves every VM vcows created powered off,
  // and the next run does not say so: `listAllDomains(0)` returns inactive
  // domains too, so `decide()` still reports them as ours and skips them, and the
  // deploy prints `nothing to create` and exits 0. There is no `start` verb --
  // recovery is `virsh start` per domain, by hand, at the site.
  autostart = true

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
      // The suffix follows the format, the way libvirt names its own: the rig
      // writes <name>_VARS.qcow2 from a qcow2 template, while a raw .fd template
      // gives .fd. Hardcoding .fd put qcow2 content under a raw name -- harmless,
      // since the declared `format` is what is read, and misleading to anyone
      // debugging it on a host whose other domains disagree.
      nv_ram = "/var/lib/libvirt/qemu/nvram/${each.value.domain_name}_VARS.${each.value.loader_format == "qcow2" ? "qcow2" : "fd"}"
      // Only what libvirt hands back. Measured on libvirt 10.0.0 (#75): its
      // formatter drops format='raw' as its own default but keeps
      // format='qcow2', and drops templateFormat for every value, including one
      // that differs from format -- that attribute is write-only. Nothing under
      // `os` is computed, so anything declared here and not returned fails the
      // apply after the volumes are already written. `format` is still sent for
      // qcow2 because libvirt does not probe a varstore, and a qcow2 one opened
      // raw does not boot.
      format   = each.value.loader_format == "raw" ? null : each.value.loader_format
      template = each.value.nvram_template
    } : null

    boot_devices = [{ dev = "hd" }]
  }

  // libvirt refuses an EFI domain without ACPI outright -- "unsupported
  // configuration: UEFI requires ACPI on this architecture" -- and since the
  // provider writes exactly the XML it is handed, nothing supplies a default.
  // APIC travels with it because that is what every x86_64 domain libvirt builds
  // for itself carries; the rig's own guests are <acpi/><apic/>, recorded in
  // tests/fixtures/libvirt/domain-unmarked-running.xml. Found in the acceptance
  // run, where both domains failed to define.
  features = {
    acpi = true
    apic = {}
  }

  // libvirt supplies no timers of its own. A minimally defined domain on this rig
  // carries `<clock offset='utc'/>` and nothing else
  // (tests/fixtures/libvirt/domain-marked.xml), while the same host's
  // virt-install guests carry exactly this set -- so it is the host's own answer,
  // not a guess. `present` is a string rather than a boolean, for the reason
  // `loader_readonly` above is: the provider's schema is the ground truth and its
  // generated docs disagree with it.
  clock = {
    offset = "utc"
    timer = [
      { name = "rtc", tick_policy = "catchup" },
      { name = "pit", tick_policy = "delay" },
      { name = "hpet", present = "no" },
    ]
  }

  devices = {
    disks = [
      {
        device = "disk"
        // `discard` because without it the guest's deletes and `fstrim` never
        // reach the qcow2, so a 40 GiB overlay only ever ratchets toward its
        // declared size -- on a pool that belongs to somebody else (D29) and that
        // nothing prunes. Not on the cdrom below, which is read-only, and no
        // `cache`/`io` alongside it: no failure is attached to those.
        driver = { name = "qemu", type = "qcow2", discard = "unmap" }
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
    //
    // The acceptance run did not bear D26 out. `virsh console` needs a
    // controlling TTY, and a pty keeps no scrollback, so it produced nothing on
    // the guest that actually needed diagnosing -- what found defect 5 was SSH
    // into the guest, which only worked because the guest was reachable at all.
    // A pty console is therefore worth roughly nothing for the failure it was
    // added for. It stays because removing it costs a redeploy of every VM and
    // buys nothing either; `<log file=.../>` on the serial device would give the
    // boot transcript for free, and is open (D26, acceptance.md "Still open")
    // because nobody has decided who owns the host path it writes to.
    serials  = [{ target = { port = 0 } }]
    consoles = [{ target = { type = "serial", port = 0 } }]

    // Same reason as the console: nothing adds one. A Rocky 9 first boot
    // generates its sshd host keys and seeds the kernel CRNG before cloud-init
    // finishes, and with no virtio-rng that comes from RDRAND alone. `backend`
    // takes `random` -- a host source path -- not a model; the provider's schema
    // has no `model` under it.
    rngs = [{ model = "virtio", backend = { random = "/dev/urandom" } }]
  }
}
