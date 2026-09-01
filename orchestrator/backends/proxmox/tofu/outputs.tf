// This block is NOT the public API. `parse_outputs` in the backend converts it
// into the inventory contract, so the module can be refactored without breaking
// every consumer of inventory.json -- which is the whole reason that seam exists.

output "vms" {
  description = "One entry per created VM, keyed by logical name."
  value = {
    for key, vm in proxmox_virtual_environment_vm.vm : key => {
      name = vm.name

      // PVE's numeric id, assigned at create time. Not the marker's id, which is
      // uuid5(VCOWS_NS, "<deployment>/<logical name>") and regenerates with no
      // state file. Destroy uses whichever the discovery path produced.
      vmid = vm.vm_id
      node = vm.node_name

      // The config echoed back, never observed: nothing here asks PVE what
      // address a guest came up on. The name says so.
      configured_address = var.vms[key].configured_address

      // The seed ISO, so a teardown reconciled against this record can see it.
      // Never the golden image: it is shared by every VM on the cluster, and
      // collecting it would break every other deployment.
      disks = [proxmox_virtual_environment_file.seed[key].id]
    }
  }
}
