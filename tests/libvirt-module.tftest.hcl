// Behavioural assertions on the libvirt module, offline.
//
// `tofu validate` type-checks the module and `tofu console` type-checks the
// tfvars; neither reads an attribute *value*, which is why twelve mutations of
// this file -- deleting the marker, deleting `backing_store`, `running = false`,
// re-introducing three of the five acceptance defects -- passed the whole suite
// green. `mock_provider` is what closes that: it satisfies the pinned provider's
// schema with generated values, so `command = apply` evaluates every expression
// in the module with nothing dialled, nothing created and no network. It is also
// the only mechanism that reaches the *computed* attributes, which is what makes
// the disk-source assertion below able to tell a path from a name.
//
// **One mutation is still not caught here and is not catchable here.** Removing
// `depends_on = [libvirt_volume.base]` from the seed volume is graph structure
// rather than a value, and no assertion can see it. That edge is what makes a
// partial apply a no-op apply instead of leaving seeds destroy can never reach;
// it stays pinned by main.tf's comment and by nothing else.

mock_provider "libvirt" {}

run "the_module_renders_what_the_acceptance_run_settled" {
  command = apply

  // -- identity -------------------------------------------------------------
  // The marker is the only identity this tool has. A module that drops it
  // produces VMs that boot, report success, and can never be torn down.
  assert {
    condition     = libvirt_domain.vm["app01"].metadata.xml == var.vms["app01"].marker_xml
    error_message = "the domain carries no marker: destroy could never find it"
  }
  assert {
    condition     = libvirt_domain.vm["app01"].name == var.vms["app01"].domain_name
    error_message = "the domain is not named what the tfvars asked for"
  }

  // -- the overlay ----------------------------------------------------------
  assert {
    condition     = libvirt_volume.overlay["app01"].backing_store.path == libvirt_volume.base[0].path
    error_message = "the overlay does not back onto the golden image: full copies, not overlays"
  }
  assert {
    condition     = libvirt_volume.overlay["app01"].backing_store.format.type == "qcow2"
    error_message = "the backing store's declared format is not qcow2"
  }
  assert {
    condition     = libvirt_volume.overlay["app01"].capacity == var.vms["app01"].disk_bytes
    error_message = "the overlay is not sized from the config: every guest gets the base image's size (A4)"
  }
  assert {
    condition     = libvirt_volume.overlay["app01"].capacity_unit == "bytes"
    error_message = "capacity_unit must match what render.py emits into disk_bytes"
  }
  assert {
    condition     = libvirt_volume.overlay["app01"].target.format.type == "qcow2"
    error_message = "the overlay's own format is not qcow2"
  }
  assert {
    condition     = libvirt_volume.base[0].target.format.type == "qcow2"
    error_message = "the base volume's format is not qcow2"
  }
  assert {
    condition     = libvirt_volume.base[0].create.content.url == var.base_volume.source
    error_message = "the base volume is not uploaded from the configured source"
  }

  // -- the seed -------------------------------------------------------------
  assert {
    condition     = libvirt_volume.seed["app01"].target.format.type == "iso"
    error_message = "declaring anything but iso makes the provider's post-apply read disagree with its own plan, after the volume is written"
  }
  assert {
    condition     = libvirt_volume.seed["app01"].create.content.url == var.vms["app01"].seed_iso
    error_message = "the seed volume is not uploaded from the ISO prepare built"
  }

  // -- firmware -------------------------------------------------------------
  assert {
    condition     = libvirt_domain.vm["app01"].os.firmware == "efi"
    error_message = "firmware is not passed through, so libvirt selects nothing"
  }
  assert {
    condition     = libvirt_domain.vm["app01"].os.nv_ram == null
    error_message = "app01 pins no loader and must be given no nvram block"
  }
  assert {
    condition     = libvirt_domain.vm["app02"].os.nv_ram.nv_ram == "/var/lib/libvirt/qemu/nvram/app02_VARS.qcow2"
    error_message = "the varstore suffix must follow the loader format, not a hardcoded .fd"
  }
  assert {
    condition     = libvirt_domain.vm["app02"].os.nv_ram.template == var.vms["app02"].nvram_template
    error_message = "the varstore is not templated from the configured file"
  }
  assert {
    condition     = libvirt_domain.vm["app02"].os.loader_readonly == "yes"
    error_message = "loader_readonly is a string, not a boolean (spike A6)"
  }
  assert {
    condition     = libvirt_domain.vm["app01"].os.loader_readonly == null
    error_message = "app01 pins no loader, so nothing about one should be emitted"
  }
  assert {
    condition     = length(libvirt_domain.vm["app01"].os.boot_devices) == 1 && libvirt_domain.vm["app01"].os.boot_devices[0].dev == "hd"
    error_message = "the domain boots something other than its disk"
  }

  // -- the domain -----------------------------------------------------------
  assert {
    condition     = libvirt_domain.vm["app01"].running == true
    error_message = "the domain is defined and never started"
  }
  assert {
    condition     = libvirt_domain.vm["app01"].cpu.mode == "host-passthrough"
    error_message = "every hypervisor is Haswell or newer; there is no migration constraint to trade guest performance for"
  }
  assert {
    condition     = libvirt_domain.vm["app01"].features.acpi == true
    error_message = "libvirt refuses an EFI domain without ACPI outright; both domains failed to define in the acceptance run"
  }
  assert {
    condition     = libvirt_domain.vm["app01"].features.apic != null
    error_message = "APIC travels with ACPI: it is what every x86_64 domain libvirt builds for itself carries"
  }

  // -- devices --------------------------------------------------------------
  assert {
    condition     = libvirt_domain.vm["app01"].devices.disks[0].source.file.file == libvirt_volume.overlay["app01"].path
    error_message = "the disk must be the overlay's computed path, never its name: destroy parses <source file=>"
  }
  assert {
    condition     = libvirt_domain.vm["app01"].devices.disks[1].source.file.file == libvirt_volume.seed["app01"].path
    error_message = "the cdrom must be the seed's computed path"
  }
  assert {
    condition     = libvirt_domain.vm["app01"].devices.disks[0].target.dev == "vda" && libvirt_domain.vm["app01"].devices.disks[0].target.bus == "virtio"
    error_message = "the root disk is not vda on virtio"
  }
  assert {
    condition     = libvirt_domain.vm["app01"].devices.disks[1].target.dev == "sda" && libvirt_domain.vm["app01"].devices.disks[1].target.bus == "sata"
    error_message = "the seed is not sda on sata"
  }
  assert {
    condition     = libvirt_domain.vm["app01"].devices.disks[1].read_only == true
    error_message = "the seed ISO is writable"
  }
  assert {
    condition     = libvirt_domain.vm["app01"].devices.disks[1].driver.type == "raw"
    error_message = "the cdrom driver type is not raw"
  }
  assert {
    condition     = length(libvirt_domain.vm["app01"].devices.serials) == 1 && libvirt_domain.vm["app01"].devices.consoles[0].target.type == "serial"
    error_message = "without a console, a VM that fails cloud-init at an air-gapped site is unreachable and un-inspectable"
  }
  assert {
    condition     = libvirt_domain.vm["app01"].devices.interfaces[0].mac.address == var.vms["app01"].nics[0].mac
    error_message = "the NIC does not carry the derived MAC the seed's network-config matches on"
  }
}
