// Behavioural assertions on the Proxmox module, offline.
//
// Same reasoning as libvirt-module.tftest.hcl: `tofu validate` type-checks the
// module and `tofu console` type-checks the tfvars, and neither reads an
// attribute *value*. `mock_provider` satisfies the pinned provider's schema with
// generated values, so `command = apply` evaluates every expression with nothing
// dialled, nothing created and no network.
//
// **The same one mutation is not catchable here.** Removing `depends_on =
// [proxmox_virtual_environment_file.image]` from the seed upload is graph
// structure rather than a value. That edge is what makes a partial apply a no-op
// apply instead of leaving seed ISOs destroy can never reach -- only VMs carry
// markers. It stays pinned by main.tf's comment and by nothing else.

// The file resource's `id` is computed, and `mock_provider` fills a computed
// string with a random one -- which the VM resource then rejects, because it
// validates `file_id` as a `datastore:content/name` volume id. That is the mock
// being unrealistic rather than the module being wrong, so the id is pinned to a
// well-formed one here. **It is one value for every file resource**, so an
// assertion that a VM mounts "its own" seed cannot be written against the id;
// the `source_file` assertions below carry that instead.
mock_provider "proxmox" {
  mock_resource "proxmox_virtual_environment_file" {
    defaults = {
      id = "local:iso/mocked-seed.iso"
    }
  }
}

run "the_module_renders_what_the_config_asked_for" {
  command = apply

  // -- identity -------------------------------------------------------------
  // The marker is the only identity this tool has, and on Proxmox it lives in
  // the description. A module that drops it produces VMs that boot, report
  // success, and can never be torn down.
  assert {
    condition     = proxmox_virtual_environment_vm.vm["app01"].description == var.vms["app01"].description
    error_message = "the VM carries no marker: destroy could never find it"
  }
  assert {
    condition     = proxmox_virtual_environment_vm.vm["app01"].name == var.vms["app01"].vm_name
    error_message = "the VM is not named what the tfvars asked for"
  }

  // `destroy.py` matches a candidate seed ISO against the basename
  // `cloudinit.seed_name` derives for the marker's logical name. A module that
  // uploads under a different name deploys clean and then makes every teardown
  // leave the ISO behind, with nothing but hand-deleting on the node.
  assert {
    condition = alltrue([
      for k, v in var.vms :
      proxmox_virtual_environment_file.seed[k].source_file[0].file_name == v.seed_name
    ])
    error_message = "a seed ISO is not uploaded under the name destroy looks for"
  }

  // Each VM's seed comes from *that* VM's ISO. This is what catches a crossed
  // `each.key`, which the file_id assertion cannot: every mocked file resource
  // shares one id.
  assert {
    condition = alltrue([
      for k, v in var.vms :
      proxmox_virtual_environment_file.seed[k].source_file[0].path == v.seed_iso
    ])
    error_message = "a VM's seed ISO was uploaded from another VM's file"
  }

  // -- the image ------------------------------------------------------------
  // One upload for the whole cluster, not one per VM.
  assert {
    condition     = length(proxmox_virtual_environment_file.image) == 1
    error_message = "the golden image was not uploaded when create was true"
  }
  assert {
    condition     = proxmox_virtual_environment_file.image[0].content_type == "import"
    error_message = "the image must use the `import` content type for import_from to work"
  }
  assert {
    condition     = proxmox_virtual_environment_file.image[0].source_file[0].path == var.image.source
    error_message = "the image is uploaded from somewhere other than the configured qcow2"
  }

  // Every VM's disk imports from that one upload rather than from a path.
  assert {
    condition = alltrue([
      for k, v in var.vms :
      proxmox_virtual_environment_vm.vm[k].disk[0].import_from == local.image_id
    ])
    error_message = "a VM's disk does not import from the golden image"
  }
  assert {
    condition = alltrue([
      for k, v in var.vms :
      proxmox_virtual_environment_vm.vm[k].disk[0].datastore_id == var.datastore
    ])
    error_message = "a VM's disk is not on the configured datastore"
  }

  // -- cloud-init -----------------------------------------------------------
  // The seed ISO is attached as a CD-ROM, which is the datasource. Two cidata
  // sources would let cloud-init pick one non-deterministically, which is why
  // there is no `initialization` block.
  assert {
    condition = alltrue([
      for k, v in var.vms :
      proxmox_virtual_environment_vm.vm[k].cdrom[0].file_id == proxmox_virtual_environment_file.seed[k].id
    ])
    error_message = "the VM does not mount its own seed ISO"
  }
  assert {
    condition     = length(proxmox_virtual_environment_vm.vm["app01"].initialization) == 0
    error_message = "PVE's own cloud-init drive is configured beside the seed ISO: cloud-init would pick one non-deterministically"
  }

  // -- networking -----------------------------------------------------------
  // cloud-init's network-config matches an interface by MAC to apply the static
  // address. A module that drops the MAC boots a guest that falls back to DHCP
  // and comes up healthy on an address nobody asked for.
  assert {
    condition = alltrue([
      for k, v in var.vms :
      proxmox_virtual_environment_vm.vm[k].network_device[0].mac_address == v.nics[0].mac
    ])
    error_message = "a NIC carries no MAC: cloud-init cannot match it and the guest falls back to DHCP"
  }
  assert {
    condition = alltrue([
      for k, v in var.vms :
      proxmox_virtual_environment_vm.vm[k].network_device[0].bridge == v.nics[0].bridge
    ])
    error_message = "a NIC is not on the configured bridge"
  }
  assert {
    condition     = proxmox_virtual_environment_vm.vm["app02"].network_device[0].vlan_id == 42
    error_message = "a configured VLAN tag did not reach the NIC"
  }

  // -- firmware -------------------------------------------------------------
  // PVE allocates the EFI vars disk itself, which is why this backend needs
  // none of libvirt's loader/nvram_template host paths.
  assert {
    condition     = length(proxmox_virtual_environment_vm.vm["app01"].efi_disk) == 1
    error_message = "an ovmf VM was given no EFI disk"
  }
  assert {
    condition = alltrue([
      for k, v in var.vms :
      proxmox_virtual_environment_vm.vm[k].bios == v.bios
    ])
    error_message = "the VM's firmware is not what the config asked for"
  }

  // -- lifecycle ------------------------------------------------------------
  // Without on_boot a node reboot leaves every VM vcows created powered off, and
  // the next run does not say so: discovery still reports them as ours.
  assert {
    condition     = proxmox_virtual_environment_vm.vm["app01"].on_boot == true
    error_message = "the VM would not come back after a node reboot"
  }
  assert {
    condition     = proxmox_virtual_environment_vm.vm["app01"].stop_on_destroy == true
    error_message = "a `tofu destroy` in the run directory could not stop the VM"
  }
  assert {
    condition     = proxmox_virtual_environment_vm.vm["app01"].node_name == var.node
    error_message = "the VM was not created on the configured node"
  }
}
