# Deploying with vcows at your site

Everything here runs from the unpacked delivery directory. The only connection
vcows opens is the one to your own hypervisor.

## 1. Load the image

Copy the whole delivery directory off the medium, then, from inside it:

    ./vcows.sh install

It verifies `SHA256SUMS` over everything delivered and loads the image into
podman. It refuses if anything is missing or has changed in transit.

## 2. Write the config

    cp config.example.yaml config.yaml
    $EDITOR config.yaml

Replace every `<...>`. The template is refused exactly as delivered, so
`./vcows.sh validate` is what tells you which placeholders are still there.
The config holds your credentials in cleartext: keep it as you would keep the
private key inside it.

## 3. The verbs

    ./vcows.sh version      what is inside the image
    ./vcows.sh validate     offline; no connection is opened
    ./vcows.sh preflight    what exists and what would be done; changes nothing
    ./vcows.sh deploy       create what does not exist
    ./vcows.sh destroy      tear this deployment down; asks first, -y answers in advance

Run them in that order the first time. `deploy` creates what is missing and
never modifies or removes, so **deleting a VM from `config.yaml` does not delete
the VM** -- `destroy` is the only teardown, and it tears down only the VMs this
deployment created.

The config is `./config.yaml`, the golden images are in `./images` and run
records go under `./runs`. `-c`, `-i` and `-r` move any of the three, and
`--run-dir DIR` makes DIR itself one run's record directory.

## 4. The run directory

Each run writes `runs/<deployment>/<timestamp>Z/`: the seed ISO each VM was
given, an inventory of what was created, a manifest of the build that ran, a
`run.json` saying what was asked and what happened, and the run's log as a file
named `log` beside it. It is created `0700` and **it carries secrets** -- the
seed ISOs contain your `user_data` verbatim.

Nothing expires them, and vcows never deletes one. That directory is the whole
account of what a run did, so it is what to send back if you need to ask about
a run, and deleting the old ones is your job.
