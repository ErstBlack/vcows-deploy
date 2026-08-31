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

  // The domain name above is the one destroy does *not* use -- discovery is by
  // marker and UUID. These two are: `_deletable` matches every candidate disk
  // against `{overlay_name(marker.name), seed_name(marker.name)}` before it will
  // unlink anything (destroy.py's `owned` set). A module naming the overlay
  // `app01` rather than `app01.qcow2` deploys clean and then makes every
  // teardown refuse every disk, with nothing left but hand-deleting on the
  // hypervisor.
  assert {
    condition = alltrue([
      for k, v in var.vms :
      libvirt_volume.overlay[k].name == v.overlay_name &&
      libvirt_volume.seed[k].name == v.seed_name
    ])
    error_message = "the volumes are not named what the tfvars asked for: destroy matches disks on exactly these two names and would refuse every one"
  }

  // -- the config values that reach the domain ------------------------------
  // Over `var.vms` rather than app01 alone, so the second VM is read by more
  // than the four assertions that name it. Each of these survives being replaced
  // with a constant in main.tf: `pool` puts every volume somewhere the config
  // never named and preflight never checked, and `memory_unit = "KiB"` is a
  // one-token edit that gives every domain 4 MiB and stops it starting.
  assert {
    condition = alltrue([
      for k, v in var.vms :
      libvirt_domain.vm[k].vcpu == v.vcpus &&
      libvirt_domain.vm[k].memory == v.memory_mib &&
      libvirt_domain.vm[k].memory_unit == "MiB" &&
      libvirt_domain.vm[k].os.type_machine == v.machine &&
      libvirt_domain.vm[k].os.type == "hvm" &&
      libvirt_domain.vm[k].os.type_arch == "x86_64"
    ])
    error_message = "a domain does not carry the sizing, machine type or arch its tfvars asked for"
  }

  // -- the domain type ------------------------------------------------------
  // Its own block rather than a clause in the sizing `alltrue` above, whose
  // message is about wrong numbers: `type = "qemu"` is TCG, so every VM
  // defines, boots, completes cloud-init and the deploy reports success --
  // unaccelerated, with nothing anywhere saying so. `type = "kvm"` on
  // `libvirt_domain.vm` is the only place the value is decided; no config
  // field, no output, no XML read.
  // scripts/smoke-libvirt.sh cannot carry this: that job runs deliberately
  // without /dev/kvm and asserts a `type = "qemu"` it overrode itself.
  assert {
    condition = alltrue([
      for k, v in var.vms : libvirt_domain.vm[k].type == "kvm"
    ])
    error_message = "a domain is not asking for KVM: it boots under TCG emulation and the deploy still reports success"
  }
  assert {
    condition = alltrue([
      for k, v in var.vms :
      libvirt_volume.overlay[k].pool == var.pool &&
      libvirt_volume.seed[k].pool == var.pool
    ])
    error_message = "a volume lands in a pool the config never named and preflight never checked"
  }
  // The base volume is the one the loop above cannot reach -- it is not per-VM --
  // so its pool and name were asserted by nothing. It alone can land in a pool
  // the config never named and preflight never checked, and a wrong name makes
  // every overlay back onto an image that is not there.
  assert {
    condition     = libvirt_volume.base[0].pool == var.pool && libvirt_volume.base[0].name == var.base_volume.name
    error_message = "the golden image is not created under the configured pool and name"
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
  // app02 by name, not `for k, v in var.vms`: it is the fixture's one VM with a
  // pinned loader, so app01's arm of every ternary here is null.
  assert {
    condition     = libvirt_domain.vm["app02"].os.loader == var.vms["app02"].loader && libvirt_domain.vm["app01"].os.loader == null
    error_message = "the loader is not the configured firmware build: a constant here boots the wrong OVMF, or none"
  }
  assert {
    condition     = libvirt_domain.vm["app02"].os.loader_type == "pflash" && libvirt_domain.vm["app01"].os.loader_type == null
    error_message = "a pinned loader must be declared pflash; anything else is not a UEFI domain"
  }
  // A qcow2 varstore is declared qcow2. libvirt keeps that value and does not
  // probe for it, so a qcow2 varstore left undeclared is opened raw -- the
  // non-booting inversion of acceptance defect S6. The raw arm of this same
  // expression is its own run block at the end of this file.
  assert {
    condition     = libvirt_domain.vm["app02"].os.nv_ram.format == var.vms["app02"].loader_format
    error_message = "the varstore's declared format does not follow the loader format: an undeclared qcow2 varstore is opened raw and does not boot"
  }
  // This assertion used to read `template_format == loader_format`, with the
  // message "libvirt reads the declared format, not the extension". That claim
  // is false for this attribute and #75's reverification measured it so: on
  // libvirt 10.0.0, `virsh define`/`dumpxml` with no provider in the loop drops
  // templateFormat from the stored XML for *every* value, including one that
  // differs from format. It is write-only. Nothing under `os` is computed, so
  // declaring it made the provider return null where the plan held "raw" and
  // killed the apply after all three volumes had been written.
  //
  // This pins what the module *emits*, which is the offline half of #75 and not
  // the whole of it -- no mock can observe what libvirt hands back.
  assert {
    condition     = libvirt_domain.vm["app02"].os.nv_ram.template_format == null
    error_message = "the module declares a varstore template_format: libvirt never echoes one back, so the apply dies after the volumes exist (#75)"
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
  // Four constants in the two comprehensions above that no assertion read. Over
  // `var.vms` so both VMs are checked: a NIC with no source gets no network at
  // all, and a root disk declared `cdrom` is not bootable.
  assert {
    condition = alltrue([
      for k, v in var.vms :
      libvirt_domain.vm[k].devices.disks[0].device == "disk" &&
      libvirt_domain.vm[k].devices.disks[0].driver.name == "qemu" &&
      libvirt_domain.vm[k].devices.interfaces[0].model.type == v.nics[0].model &&
      libvirt_domain.vm[k].devices.interfaces[0].source.network.network == v.nics[0].network
    ])
    error_message = "a domain's root disk or NIC is not what its tfvars asked for: a cdrom root disk does not boot, and a NIC with no source reaches no network"
  }

  // -- what libvirt does not supply ----------------------------------------
  // Four settings the provider writes only because the module names them. Each
  // has a failure that is invisible on the day of the deploy: the VMs come up,
  // the run reports success, and the cost arrives at a reboot, a full pool, or a
  // first boot short of entropy.
  assert {
    condition     = libvirt_domain.vm["app01"].autostart == true
    error_message = "autostart is off: a host reboot leaves every VM down and the next deploy prints `nothing to create`"
  }
  assert {
    condition     = libvirt_domain.vm["app01"].devices.disks[0].driver.discard == "unmap"
    error_message = "the overlay disk passes no discard: guest deletes never return blocks and the overlay only grows"
  }
  // The cdrom's driver type is asserted below and the overlay volume's own format
  // above; these two have to agree. Declaring the root disk `raw` hands the guest
  // the qcow2 header as its first sector, so every VM in the deployment fails to
  // boot after a run that reported success.
  assert {
    condition     = libvirt_domain.vm["app01"].devices.disks[0].driver.type == "qcow2"
    error_message = "the overlay is presented to the guest as raw: the qcow2 header becomes sector 0 and nothing boots"
  }
  assert {
    condition     = length(libvirt_domain.vm["app01"].devices.rngs) == 1 && libvirt_domain.vm["app01"].devices.rngs[0].model == "virtio"
    error_message = "no virtio-rng: a first boot seeds its CRNG and sshd host keys from RDRAND alone"
  }
  assert {
    condition     = libvirt_domain.vm["app01"].devices.rngs[0].backend.random == "/dev/urandom"
    error_message = "the rng backend names no host source, so the device has nothing to read"
  }
  assert {
    condition     = libvirt_domain.vm["app01"].clock.offset == "utc"
    error_message = "the guest clock does not follow the host in UTC"
  }
  assert {
    condition     = [for t in libvirt_domain.vm["app01"].clock.timer : t.name] == ["rtc", "pit", "hpet"]
    error_message = "the timer set is not the one this rig's own virt-install guests carry"
  }
  assert {
    condition     = libvirt_domain.vm["app01"].clock.timer[2].present == "no"
    error_message = "`present` is a string here, as `loader_readonly` is: a boolean does not render"
  }

  // -- the inventory's half of the contract --------------------------------
  assert {
    condition     = output.vms["app01"].configured_address == var.vms["app01"].configured_address
    error_message = "the inventory output does not carry the configured address"
  }
  assert {
    condition     = !contains(keys(output.vms["app01"]), "address")
    error_message = "`address` reads as something libvirt was asked; nothing here observes one"
  }
}

// -- the two branches the golden tfvars does not take -------------------------
//
// tests/golden/libvirt.tfvars.json is copied in as main.auto.tfvars.json, so the
// block above is fed exactly what `_deploy` writes. That is its authority, and
// it is also its limit: the fixture sets `base_volume.create = true` and gives
// every NIC `network` with `bridge = null`, so three expressions in shipped
// module code are never evaluated by it -- `libvirt_volume.base`'s `count` zero
// arm, `local.base_path`'s fallback to var.base_volume.path, and the non-null
// arm of the `source.bridge` ternary in the domain's `interfaces` block.
//
// The bridge arm is not merely uncovered, it is the shipped design.
// findings.md settles the network as "bridged, static IPs from config", and
// config.py's module docstring names the "exactly one of bridge/network" check
// while render.py's `_nic` emits both with the unused one null. The tested
// path is the one a real RHEL 9 site does not take.
//
// **These two blocks are a weaker kind of test than the one above, and the
// difference is worth naming.** A run-level `variables` block overrides a
// variable wholesale rather than merging into it, so these are hand-written
// rather than fed from the fixture, and nothing asserts they still resemble
// what render.py emits. They carry the minimum each branch needs and no more.

run "a_prebuilt_base_volume_is_used_in_place" {
  command = apply

  variables {
    base_volume = {
      create = false
      name   = "golden.qcow2"
      path   = "/var/lib/libvirt/images/golden.qcow2"
      source = ""
    }
  }

  assert {
    condition     = length(libvirt_volume.base) == 0
    error_message = "create = false still built a base volume: a second upload of an image the pool already holds"
  }
  assert {
    condition = alltrue([
      for k, v in var.vms :
      libvirt_volume.overlay[k].backing_store.path == var.base_volume.path
    ])
    error_message = "the overlay does not back onto the pool's existing image when create is false: local.base_path's fallback is not reached"
  }
}

run "a_bridged_nic_renders_source_bridge" {
  command = apply

  variables {
    vms = {
      app01 = {
        configured_address = "192.168.122.60"
        disk_bytes         = 42949672960
        domain_name        = "app01"
        firmware           = "efi"
        loader             = null
        loader_format      = null
        machine            = "q35"
        marker_xml         = "<vcows xmlns=\"urn:vcows:1\">{\"v\":\"0.1.0.0\",\"deployment\":\"lab-a\",\"name\":\"app01\",\"id\":\"2647c9f3-9d71-531a-b874-98a578d6c7aa\"}</vcows>"
        memory_mib         = 4096
        nics = [
          {
            bridge  = "br0"
            mac     = "52:54:00:be:a8:60"
            model   = "virtio"
            network = null
          }
        ]
        nvram_template = null
        overlay_name   = "app01.qcow2"
        seed_iso       = "/run/vcows/lab-a/app01-seed.iso"
        seed_name      = "app01-seed.iso"
        vcpus          = 2
      }
    }
  }

  assert {
    condition     = libvirt_domain.vm["app01"].devices.interfaces[0].source.bridge.bridge == var.vms["app01"].nics[0].bridge
    error_message = "a bridged NIC does not render <source bridge=>: the guest lands on the wrong network, or on none"
  }
  assert {
    condition     = libvirt_domain.vm["app01"].devices.interfaces[0].source.network == null
    error_message = "a bridged NIC also rendered <source network=>: two sources on one interface is XML libvirt will not take"
  }
}

run "a_bios_domain_is_given_no_firmware_and_no_varstore" {
  command = apply

  // The third branch the golden tfvars does not take, and the one 454ee7c's
  // header above did not name. Both fixture VMs carry `firmware = "efi"`, so
  // the null arm of main.tf's `firmware` ternary is never evaluated by them --
  // yet `bios` is a value an operator can write today (schema.py's `firmware`
  // is {"enum": ["efi", "bios"]}).
  // Same caveat as the two blocks above: `variables` overrides wholesale rather
  // than merging, so this VM is hand-written and nothing asserts it still
  // resembles what render.py emits.
  //
  // This is *evaluation* coverage and nothing more. It proves main.tf produces
  // the right value on the BIOS branch; it cannot prove libvirt echoes that
  // value back the way the provider planned it, which is #75 and needs a real
  // libvirtd. A green run here must not be read as having closed that.
  variables {
    vms = {
      app01 = {
        configured_address = "192.168.122.60"
        disk_bytes         = 42949672960
        domain_name        = "app01"
        firmware           = "bios"
        loader             = null
        loader_format      = null
        machine            = "q35"
        marker_xml         = "<vcows xmlns=\"urn:vcows:1\">{\"v\":\"0.1.0.0\",\"deployment\":\"lab-a\",\"name\":\"app01\",\"id\":\"2647c9f3-9d71-531a-b874-98a578d6c7aa\"}</vcows>"
        memory_mib         = 4096
        nics = [
          {
            bridge  = null
            mac     = "52:54:00:be:a8:60"
            model   = "virtio"
            network = "default"
          }
        ]
        nvram_template = null
        overlay_name   = "app01.qcow2"
        seed_iso       = "/run/vcows/lab-a/app01-seed.iso"
        seed_name      = "app01-seed.iso"
        vcpus          = 2
      }
    }
  }

  assert {
    condition     = libvirt_domain.vm["app01"].os.firmware == null
    error_message = "a bios domain still asks libvirt for efi: the null arm of main.tf's `firmware` ternary is not reached"
  }
  assert {
    condition     = libvirt_domain.vm["app01"].os.nv_ram == null
    error_message = "a bios domain carries a varstore, which is a UEFI-only device"
  }
}

run "a_raw_loader_declares_no_format_libvirt_would_drop" {
  command = apply

  // The fourth branch the golden tfvars does not take. app02 pins `qcow2`, so
  // the raw arm of main.tf's `nv_ram.format` ternary and the `.fd` arm of its
  // `nv_ram` suffix are evaluated by nothing else in this suite -- and raw is
  // the RHEL shape (variables.tf's loader comment), the one the tool is being
  // built for. Same caveat
  // as the three blocks above: `variables` overrides wholesale rather than
  // merging, so this VM is hand-written and nothing asserts it still resembles
  // what render.py emits.
  //
  // Two of the three assertions below are the offline half of #75, and half is
  // all an offline gate gets. They pin that the module *stops emitting* the two
  // attributes libvirt does not hand back. Whether libvirt hands them back is a
  // property of libvirtd and the provider binary that no mock can observe; that
  // half is scripts/smoke-libvirt.sh, which pins this same branch against a real
  // libvirtd. A green run here must not be read as having closed #75.
  variables {
    vms = {
      app03 = {
        configured_address = "192.168.122.62"
        disk_bytes         = 42949672960
        domain_name        = "app03"
        firmware           = "efi"
        loader             = "/usr/share/edk2/ovmf/OVMF_CODE.fd"
        loader_format      = "raw"
        machine            = "q35"
        marker_xml         = "<vcows xmlns=\"urn:vcows:1\">{\"v\":\"0.1.0.0\",\"deployment\":\"lab-a\",\"name\":\"app03\",\"id\":\"2647c9f3-9d71-531a-b874-98a578d6c7aa\"}</vcows>"
        memory_mib         = 4096
        nics = [
          {
            bridge  = null
            mac     = "52:54:00:be:a8:62"
            model   = "virtio"
            network = "default"
          }
        ]
        nvram_template = "/usr/share/edk2/ovmf/OVMF_VARS.fd"
        overlay_name   = "app03.qcow2"
        seed_iso       = "/run/vcows/lab-a/app03-seed.iso"
        seed_name      = "app03-seed.iso"
        vcpus          = 2
      }
    }
  }

  assert {
    condition     = libvirt_domain.vm["app03"].os.nv_ram.format == null
    error_message = "a raw varstore is declared format='raw': libvirt omits its own default, so the provider returns null where the plan held a string and the apply dies after the volumes are written (#75)"
  }
  assert {
    condition     = libvirt_domain.vm["app03"].os.nv_ram.template_format == null
    error_message = "the module declares a varstore template_format: libvirt never echoes one back, for any value, so the apply dies after the volumes exist (#75)"
  }
  // The `.fd` arm of main.tf's `nv_ram` suffix, and the offline half of
  // docs/rhel9-target.md's C2.
  // A hardcoded .fd was the old bug here; a hardcoded .qcow2 would be the same
  // bug inverted, and only a raw fixture can tell the two apart.
  assert {
    condition     = libvirt_domain.vm["app03"].os.nv_ram.nv_ram == "/var/lib/libvirt/qemu/nvram/app03_VARS.fd"
    error_message = "a raw template's varstore is not named .fd: the suffix is following something other than the loader format"
  }
}
