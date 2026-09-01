// Every value OpenTofu needs that is not structural arrives through here, as
// `.auto.tfvars.json` written by orchestrator/backends/proxmox/render.py. The
// module itself is hand-written and never generated -- which is what makes
// `tofu validate` a real gate rather than a check that a generator agrees with
// itself. A typo in a variable name fails here, offline, without contacting
// anything.
//
// There is no credential variable. The provider reads PROXMOX_VE_API_TOKEN from
// its own environment, which orchestrator/tofu.py passes through untouched, so
// nothing secret is written to the run directory.

variable "endpoint" {
  type        = string
  description = "PVE API base URL, https only. Carries no credentials and no query string: both are refused before render, because the netloc travels verbatim into the tfvars that sit in the run directory."
}

variable "insecure" {
  type        = bool
  description = "Skip TLS verification. Warned about by validate: the API token is a bearer credential and this sends it to whatever answers. There is no ca_file counterpart -- bpg/proxmox 0.111.1 has no CA-bundle option, so a private CA goes in SSL_CERT_FILE (provider) and REQUESTS_CA_BUNDLE (vcows own calls)."
}

variable "node" {
  type        = string
  description = "The node to create on. Discovery is cluster-wide -- a migrated VM is still ours -- but creation is pinned here, because choosing a node by scheduling policy is a decision nobody has made."
}

variable "datastore" {
  type        = string
  description = "Where VM disks land. Must allow the `images` content type; preflight refuses otherwise."
}

variable "import_datastore" {
  type        = string
  description = "Where the golden image and the seed ISOs are uploaded. Must allow both `import` and `iso`. **`import` is not enabled by default on a PVE storage** -- it is added under Datacenter -> Storage -- so preflight checks it and fails fast rather than letting the apply discover it."
}

variable "image" {
  description = "The shared golden image on this cluster. Uploaded once, not once per VM."
  type = object({
    file_name = string
    // False once the image is already there. Each apply runs against a fresh,
    // empty state, so without this it would re-upload a multi-GB image on every
    // deploy after the first.
    create = bool
    // PVE's own volume id for the file, which is what `import_from` takes.
    volid = string
    // Local path to the golden qcow2, when create is true.
    source = string
    // The declared sha256, or empty. The provider verifies it after upload.
    checksum = string
  })
}

variable "vms" {
  description = "Keyed by logical name. Already narrowed to what will be created -- VMs that exist are excluded before this point, by the ownership policy in orchestrator/backends/base.py."
  type = map(object({
    vm_name   = string
    seed_name = string

    // The ownership marker, as one prefixed line in the VM's description. It is
    // the durable record of what vcows created, and what destroy discovers by.
    description = string

    vcpus      = number
    memory_mib = number
    disk_gb    = number

    // PVE's vocabulary: ovmf or seabios, translated in render.py from the
    // config's efi/bios so one operator reads both backends' configs.
    bios    = string
    machine = string
    os_type = string

    // Local path to this VM's cidata ISO, built by orchestrator/cloudinit.py.
    seed_iso = string

    // What the config said, for the inventory. The tool never asks PVE what
    // address a guest came up on, which is what the name records.
    configured_address = string

    nics = list(object({
      mac    = string
      model  = string
      bridge = string
      // Null rather than omitted: a map of objects in HCL must have a uniform
      // shape, the same reason the libvirt module's NIC carries both halves of
      // its union.
      vlan_id = optional(number)
    }))
  }))
}
