// The fake backend's module, and the reason `vcows deploy` can be tested end to
// end at all. `tests/fake_backend.py` proves the seam by having no hypervisor
// semantics; this is what its `render()` output applies against.
//
// `terraform_data` is builtin, so `tofu init` here installs nothing, contacts
// nothing, and needs no provider mirror -- which is what lets the CLI gate run
// the real binary with no network and no hypervisor.

variable "endpoint" {
  type = string
}

variable "seed" {
  type = string
}

variable "vms" {
  type = map(object({
    marker_xml = string
  }))
}

resource "terraform_data" "vm" {
  for_each = var.vms
  input = {
    name       = each.key
    marker_xml = each.value.marker_xml
    endpoint   = var.endpoint
    seed       = var.seed
  }
}

// Shaped like the libvirt module's: not the public API, converted by the
// backend's `parse_outputs` into the inventory contract.
output "vms" {
  value = {
    for key, vm in terraform_data.vm : key => {
      name = key
      id   = vm.id
    }
  }
}
