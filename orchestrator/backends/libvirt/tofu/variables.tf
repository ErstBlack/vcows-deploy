// Every value OpenTofu needs that is not structural arrives through here, as
// `.auto.tfvars.json` written by orchestrator/backends/libvirt/render.py. The
// module itself is hand-written and never generated -- which is what makes
// `tofu validate` a real gate rather than a check that a generator agrees with
// itself. A typo in a variable name fails here, offline, without contacting
// anything.

variable "uri" {
  type        = string
  description = "libvirt connection URI. qemu+ssh:// form, with the SSH options vcows assembled from ssh_keyfile and known_hosts. The operator never supplies a query string."
}

variable "pool" {
  type        = string
  description = "Storage pool name. Must already exist and be active; preflight refuses otherwise. vcows never creates a pool."
}

variable "base_volume" {
  description = "The shared golden image on this host. Uploaded once per host, not once per VM -- vol-upload streams the whole image through the SSH tunnel with no resume."
  type = object({
    name = string
    // False once the image is already on this host. Each apply runs against a
    // fresh, empty state, so without this it would try to create an existing
    // volume on every deploy after the first.
    create = bool
    // The pool's own path for it, when create is false.
    path = string
    // Local path to the golden qcow2, when create is true.
    source = string
  })
}

variable "vms" {
  description = "Keyed by logical name. Already narrowed to what will be created -- VMs that exist are excluded before this point, by the ownership policy in orchestrator/backends/base.py."
  type = map(object({
    domain_name  = string
    overlay_name = string
    seed_name    = string

    // The ownership marker, verbatim. It is one namespaced element whose text is
    // the JSON payload, and it lands inside <metadata> in the XML handed to
    // DomainDefineXML. Malformed XML fails at define time, which is the right
    // failure mode.
    marker_xml = string

    vcpus      = number
    memory_mib = number

    // Capacity belongs on the overlay and nowhere else: vol-upload writes the
    // golden image's header from offset 0 and silently discards whatever the
    // base volume declared. Confirmed in spike A4.
    disk_bytes = number

    // Local path to this VM's cidata ISO, built by prepare.py.
    seed_iso = string

    firmware = string
    machine  = string

    // Host-specific and unset by default, in which case libvirt selects the
    // firmware from the host's own descriptors. Fedora ships
    // OVMF_CODE_4M.qcow2; RHEL ships a raw .fd; an early RHEL 9 may carry
    // neither the descriptors nor the same paths.
    loader         = optional(string)
    loader_format  = optional(string)
    nvram_template = optional(string)

    // What the config said, for the inventory. The tool never asks libvirt for
    // an address.
    address = string

    nics = list(object({
      mac   = string
      model = string
      // Exactly one of these is non-null. Both are present because a ternary
      // between two differently-shaped objects does not type-check in HCL.
      network = optional(string)
      bridge  = optional(string)
    }))
  }))
}
