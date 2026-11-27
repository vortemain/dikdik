# Working on another computer over SSH

The environment pill in the composer (**Local**, next to the permission mode, in Code
mode) chooses where work runs. Pick a host there, or **Add SSH connection…**, to open a
folder on another computer (a dev server, a cloud VM, a machine under your desk) and get a
Dikdik window that works there:
files, commands, tests, the index, Git and the agent all run on that host. It works
like VS Code Remote-SSH. The design is ADR-009 in [DECISIONS.md](DECISIONS.md).

## What you need

- **On this computer:** the OpenSSH client (`ssh`), which Windows 10+, macOS and
  Linux include. Dikdik uses your `~/.ssh/config`, keys, ssh-agent, `ProxyJump` and
  `known_hosts` exactly as a terminal would.
- **On the host:** Linux or macOS with a POSIX shell and **Python 3.13 or newer** on
  the `PATH` as `python3.13`, `python3.14`, `python3` or `python`. Nothing else: Dikdik
  copies itself over the SSH connection.
- **Key sign-in.** Dikdik never asks for or stores passwords. If the host refuses the
  key, create one and install it:

  ```bash
  ssh-keygen -t ed25519
  ssh-copy-id user@host      # or add ~/.ssh/id_ed25519.pub to ~/.ssh/authorized_keys on the host
  ssh-add                    # if the key has a passphrase
  ```

## Connecting

1. In Code mode, click **Local** in the composer. The menu lists this computer, live SSH
   connections and the hosts saved in `~/.ssh/config`. Pick a host, or choose
   **Add SSH connection…** (also in Projects → **Connect to host…**).
2. In the Connect to host dialog, type `user@host` (or `ssh://user@host:2222`) if you
   did not pick one, and the folder on the host, such as `~/projects/app`.
3. **First connection only:** Dikdik shows the host's key fingerprint. Compare it with
   the host's own (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the host, or ask
   whoever runs it). **Trust and connect** saves it to your `known_hosts`.
4. Dikdik checks Python, installs itself into `~/.dikdik-remote/<version>/` on the host
   (only when this version is not there yet), starts there on the host's loopback
   address, and opens an SSH tunnel to it.
5. Press **Open** (or pick `SSH: host` in the environment menu). A window at
   `http://localhost:<port>` opens with `SSH: host` in its title bar and composer. In the
   desktop app the same window moves to the host. On this computer, the title bar shows
   how many hosts are connected.

## Repositories

To work on a repository on a host, choose **Repository** in the Connect to host dialog
(or **Open a repository on a host…** in the environment menu). Pick one of your GitHub
repositories, listed from this computer's GitHub CLI sign-in, or paste any https,
ssh or `git@host:path` URL. Then set **Clone into**, a folder on the host (default
`~/projects`). The host clones it with its own Git and sign-in (`gh auth login` or an
SSH key there; for SSH URLs, connect from the host to the Git server once so its key is
known), and Dikdik opens the clone. Connecting again to the same repository reuses the
existing clone; a different folder with the same name is never overwritten.

The environment menu's **Clone a repository…** clones where the window works: onto this
computer in a local window, onto the host in a remote window.

## Disconnecting

In the remote window, click `SSH: host` in the title bar or choose **Disconnect** in its
composer's environment menu; on this computer, use **Manage SSH connections** →
**Disconnect**. Dikdik stops on the host and the tunnel closes; anything still
running there stops too. Quitting Dikdik on this computer also disconnects every host.

## What runs where

| On the host | On this computer |
| --- | --- |
| Files, commands, tests, the index, Git (with the host's Git credentials) | The SSH connections and tunnels |
| Model calls, with API keys set in the **remote window's** Settings | Your local projects, keys and sign-in |
| Dikdik's state, in `~/.dikdik-remote/data` | |

Delete `~/.dikdik-remote` on the host to remove everything Dikdik put there.

## Security

- Both ends listen on loopback only; the tunnel is the only way in.
- Every SSH call is non-interactive with strict host-key checking. An unknown key is
  read by a probe that offers no credentials and is saved only after you confirm its
  fingerprint. A **changed** host key stops the connection; if the change is expected,
  remove the old key with `ssh-keygen -R <hostname>` and connect again.
- The host's sign-in token travels only inside the SSH channel and to your signed-in
  Dikdik window; it is never logged, and API keys cannot fetch it. On the host,
  Dikdik requires that token even from loopback, because other users of a shared
  server share its loopback.
- The remote window uses `localhost`, not `127.0.0.1`, so your local Dikdik sign-in
  cookie is never sent to the host.

## Errors

| Message | What to do |
| --- | --- |
| refused the SSH key sign-in | Set up a key as above, or `ssh-add` a key with a passphrase. |
| is not in your known_hosts / Could not read the host key | Through a jump host, connect to the jump host once in a terminal first. |
| host key has changed | Check with whoever runs the host before removing the old key. |
| has Python 3.x / no Python | Install Python 3.13+ on the host (package manager, pyenv or uv). |
| no POSIX shell | Windows hosts are not supported yet. |
| could not be found / did not answer / refused the connection | Check the name, network, VPN, port or `ProxyJump`. |

## Limits and next steps

Not in this first version: sending model calls back to this computer over `ssh -R` so
API keys stay here; listing repositories with the host's own `gh` sign-in; WSL
distributions; Windows hosts; and a self-contained build for hosts without Python 3.13.
Connections are not restored after Dikdik restarts.

## Status

Covered by `tests/test_ssh_remote.py` with a stand-in `ssh` (`tests/fixtures/fake_ssh.py`)
that runs the real install and the real server start on this computer and forwards
ports itself, and by `tests/web/ssh-remote.test.ts`. A connection to a real SSH server
has not been tried yet.
