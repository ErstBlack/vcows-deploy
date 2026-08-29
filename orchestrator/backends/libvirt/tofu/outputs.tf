// This block is NOT the public API. `parse_outputs` in the backend converts it
// into the inventory contract, so the module can be refactored without breaking
// every consumer of inventory.json -- which is the whole reason that seam exists.

output "vms" {
  description = "One entry per created VM, keyed by logical name."
  value = {
    for key, dom in libvirt_domain.vm : key => {
      name = dom.name

      // libvirt's own domain UUID, which is not the marker's id. The marker's is
      // uuid5(VCOWS_NS, logical name) and regenerates with no state file; this
      // one is assigned by libvirt at define time. Destroy uses whichever the
      // discovery path produced.
      uuid = dom.uuid

      address = var.vms[key].address

      // Both per-VM volumes, so a teardown reconciled against this record can
      // see the seed ISO as well as the overlay. Never the base image: it only
      // ever appears as a <backingStore>, and following that chain would destroy
      // the volume every other deployment's overlays depend on.
      disks = [
        libvirt_volume.overlay[key].path,
        libvirt_volume.seed[key].path,
      ]
    }
  }
}

output "base_volume_path" {
  description = "Where the golden image lives on this host, whether this run uploaded it or found it."
  value       = local.base_path
}
